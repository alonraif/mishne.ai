"""The worker: take a queued job, run it, settle the money.

One process, one job. In production Step Functions invokes it per stage; the
in-process runner executes the same stages in the same order, which is what
makes the machine a scheduler rather than a second implementation of the
pipeline. Locally it is:

    python -m mishne.orchestration.worker --org org_7fa2 job_a1b2

What it is responsible for that the runner is not:

* **Materialising the assets.** ffmpeg takes argv and pyaaf2 seeks around inside
  structured storage, so the objects are staged to local disk first (ADR-0013),
  and a linked AAF gets its companions staged beside it under their own names,
  which is the whole of that resolution (ADR-0014).
* **The money.** Settle at `min(actual, approved_cap)` on success, release the
  whole hold on failure or cancellation. A job that dies without releasing its
  hold leaves a customer's balance wrong until somebody notices (ADR-0006).
* **The artifacts.** Published to the artifacts bucket, and recorded as rows.
* **The transcript.** The beats, the speakers, this job's scores and its cut,
  into the tables the API reads. Until this existed, `repository.get_transcript`
  had no writer at all: every row those tables had ever held came from the seed,
  so `GET /v1/jobs/{id}/transcript` answered 404 for every job that had actually
  run, and the cut editor had nothing to edit.
* **The cost.** What the job spent on models, projected onto `jobs.cost_cents`
  from the rows the sink wrote per step. This is not the same number as the
  credits charged: the customer pays for source hours at their tier's rate, and
  what the work cost us is the other side of that trade. Until C3 the worker
  built a `Router`, let it spend money, and threw the ledger away — so the
  margin on a job was not a hard number anywhere.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import sqlalchemy as sa

from .. import alerts, telemetry
from ..config import Settings, get_settings, load_env_file
from ..db import ids as id_space
from ..db import jobs as job_writes
from ..db import models as m
from ..db import transcripts as transcript_writes
from ..db import uploads
from ..db.base import session_for_org
from ..llm import Router
from ..pipeline.project import CACHE_VERSION
from ..logging import get_logger
from ..storage import ObjectRef, derived_key
from ..workspace import S3Workspace, SourceFile
from .graph import AssetSource, JobRequest
from .runner import Cancelled, run_job
from .sink import DatabaseSink

log = get_logger(__name__)


def _asset_ids(assets: list[AssetSource]) -> dict[str, str]:
    """Each asset's pipeline id to the `assets.id` row it was staged from.

    The pipeline works in content digests and every id column in the database is
    a foreign key into `assets`, so something has to hold both halves. This is
    it: the worker is the only component that knows which row a set of bytes
    came from (`db/ids.py`).
    """
    return {src.pipeline_id: src.asset_id for src in assets}


def _job_row(s, org_id: str, job_id: str):
    jobs = m.Job.__table__
    return s.execute(
        sa.select(jobs).where(jobs.c.org_id == org_id, jobs.c.id == job_id)
    ).first()


def _assets_for(s, org_id: str, job_id: str) -> list:
    """The job's uploads, in the order they were added.

    Upload order is all "chronological" can honestly mean for material shot on
    different days (ADR-0008).
    """
    assets, join = m.Asset.__table__, m.JobAsset.__table__
    return list(
        s.execute(
            sa.select(assets, join.c.order_idx)
            .join(join, join.c.asset_id == assets.c.id)
            .where(join.c.org_id == org_id, join.c.job_id == job_id)
            .order_by(join.c.order_idx)
        ).all()
    )


def _companions(s, org_id: str, asset_id: str) -> list:
    """The media a linked AAF is waiting for, as assets in their own right."""
    from ..db import requirements as reqs

    ids = reqs.satisfied_by(s, org_id, asset_id)
    if not ids:
        return []
    assets = m.Asset.__table__
    return list(
        s.execute(
            sa.select(assets).where(assets.c.org_id == org_id, assets.c.id.in_(ids))
        ).all()
    )


def prepare_request(
    org_id: str, job_id: str, settings: Settings | None = None
) -> tuple[JobRequest, dict]:
    """Everything the runner needs, staged and ready. Reads the database once."""
    settings = settings or get_settings()
    with session_for_org(org_id) as s:
        job = _job_row(s, org_id, job_id)
        if job is None:
            raise ValueError("no such job")
        rows = _assets_for(s, org_id, job_id)
        if not rows:
            raise ValueError("a job with no assets cannot be run")
        companions = {r.id: _companions(s, org_id, r.id) for r in rows}
        approved_cap = float(job.approved_cap or 0)
        brief = dict(job.brief or {})
        notes = job.notes_raw
        # A manual or hybrid job that is queued and already has selections is
        # being resumed: a person marked the cut and `POST /jobs/{id}/cut` put
        # it back in the queue. An AI job's selections are the record of a cut
        # it has already made, which is why the mode is part of the condition.
        user_cut = (
            _submitted_cut(s, org_id, job_id) if job.mode in ("manual", "hybrid")
            else []
        )
        orgs = m.Org.__table__
        tier = s.execute(
            sa.select(orgs.c.tier).where(orgs.c.id == org_id)
        ).scalar_one()

    workspace = S3Workspace(
        org_id=org_id,
        project_id=rows[0].project_id,
        scratch=Path(settings.work_root) / job_id,
        settings=settings,
    )

    sources: list[AssetSource] = []
    for row in rows:
        staged = workspace.materialise(
            row.checksum or row.id,
            SourceFile(name=row.filename, ref=ObjectRef(row.s3_bucket, row.s3_key)),
            companions=[
                SourceFile(name=c.filename, ref=ObjectRef(c.s3_bucket, c.s3_key))
                for c in companions.get(row.id, [])
            ],
        )
        sources.append(
            AssetSource(
                asset_id=row.id,
                path=staged,
                # `staged` is a scratch copy under a sanitised name; the
                # artifacts must name the customer's file, not ours.
                display_name=row.filename,
                # The probe from upload, so a warm ingest cache written before
                # frame size was recorded still produces a truthful AAF
                # descriptor instead of the writer's 1920x1080 default.
                width=int((row.probe or {}).get("width") or 0),
                height=int((row.probe or {}).get("height") or 0),
                # The pipeline's id is the content hash, not the row id: the
                # same rushes in two projects are two rows and one ingest.
                content_id=f"a_{row.checksum[:24]}" if row.checksum else "",
            )
        )

    # The stored cut is in the database's id space and stage 8 matches it
    # against beats the pipeline named after their content, so it is translated
    # here — the one place that holds both halves (`db/ids.py`).
    assets = _asset_ids(sources)
    user_cut = [id_space.pipeline_id(beat_id, assets) for beat_id in user_cut]

    request = JobRequest(
        job_id=job_id,
        org_id=org_id,
        project_id=rows[0].project_id,
        assets=sources,
        out_dir=workspace.root / "out",
        work_dir=workspace,
        notes=notes,
        target_duration_s=brief.get("target_duration_s"),
        mode=job.mode,
        handle_frames=brief.get("handle_frames", 0),
        language=brief.get("language"),
        stem=f"{rows[0].filename.rsplit('.', 1)[0]}_roughcut",
        router=Router(),
        user_cut=user_cut,
    )
    return request, {"approved_cap": approved_cap, "workspace": workspace,
                     "tier": tier}


def _submitted_cut(s, org_id: str, job_id: str) -> list[str]:
    """The beat ids of this job's stored cut, in cut order."""
    sel = m.Selection.__table__
    return list(
        s.execute(
            sa.select(sel.c.beat_id)
            .where(sel.c.org_id == org_id, sel.c.job_id == job_id)
            .order_by(sel.c.order_idx)
        ).scalars()
    )


def execute(org_id: str, job_id: str, settings: Settings | None = None) -> str:
    """Run one job to a terminal state. Returns that state."""
    settings = settings or get_settings()
    request, meta = prepare_request(org_id, job_id, settings)
    workspace = meta["workspace"]
    cap = meta["approved_cap"]
    sink = DatabaseSink(org_id, job_id)

    try:
        result = run_job(request, sink)
    except Cancelled:
        with session_for_org(org_id) as s:
            job_writes.release(s, org_id, job_id, cap, reason="job cancelled")
            # Cancellation is checked between steps, so a job cancelled late
            # has already paid for the stages that ran.
            _record_cost(s, org_id, job_id, request)
        log.info("job.cancelled", job_id=job_id)
        workspace.cleanup()
        return "cancelled"
    except Exception as exc:  # noqa: BLE001 - every failure releases the hold
        return _failed(exc, workspace, org_id, job_id, request, cap)

    # Everything past here is also inside the failure handler, because it also
    # spends the customer's money. `_publish` writing a row it cannot write is
    # exactly as much a failed job as a stage raising, and for a while it was
    # not treated as one: the artifact insert used the format's display label
    # where the column takes its identifier, the exception left `execute`
    # unhandled, and the only thing downstream — `devrunner._fail` — set the
    # status and not the ledger. Every manual job ended `failed` with its hold
    # stranded. A `try` that stops at the last stage protects the pipeline and
    # not the transaction that bills for it.
    try:
        if result.paused:
            return _pause(result, workspace, org_id, job_id, request, cap)

        published = _publish(result, workspace, org_id, job_id, request)
        _record_transcript(result, org_id, job_id)
        actual = _credits_used(result, meta["tier"])
        with session_for_org(org_id) as s:
            charged = job_writes.settle(s, org_id, job_id, actual, cap)
            cost_cents = _record_cost(s, org_id, job_id, request)
            job_writes.set_status(
                s, org_id, job_id, "complete", finished_at=sa.func.now()
            )
    except Exception as exc:  # noqa: BLE001
        return _failed(exc, workspace, org_id, job_id, request, cap)

    log.info("job.settled", job_id=job_id, charged=charged, artifacts=published,
             model_cost_cents=cost_cents)
    with session_for_org(org_id) as s:
        moved = alerts.spend_moved(s, org_id, job_id, cost_cents=cost_cents)
    if moved:
        moved.emit()
    workspace.cleanup()
    return "complete"


def _error_facts(exc: Exception) -> dict:
    """A failure as the facts that may outlive it — see `sink._error_facts`."""
    facts: dict = {"code": type(exc).__name__}
    status = int(getattr(exc, "status", 0) or 0)
    if status:
        facts["status"] = status
    step = getattr(exc, "step", "")
    if step:
        # Which stage, so a failed job's row says where without a join. The
        # runner sets this on the exception before it propagates.
        facts["step"] = step
    return facts


def _failed(exc: Exception, workspace, org_id: str, job_id: str,
            request: JobRequest, cap: float) -> str:
    """The one end for a job that will not be delivered: hold released, failed.

    Shared by the stage failures and by everything the completion path does
    afterwards, so that there is a single answer to "what happens to the money"
    rather than one per place an exception can appear.
    """
    with session_for_org(org_id) as s:
        job_writes.release(s, org_id, job_id, cap, reason="job failed")
        # A failed job costs the customer nothing and costs us whatever it
        # spent before it died. Recording only successful jobs' costs is
        # how a retry storm stays invisible in the cost model.
        _record_cost(s, org_id, job_id, request)
        job_writes.set_status(
            s, org_id, job_id, "failed",
            # The type, not the message: an exception can quote a filename. A
            # vendor's status code can not, and it is the difference between
            # "wait and resubmit" and "this job will never run".
            error=_error_facts(exc),
            finished_at=sa.func.now(),
        )
    log.warning("job.failed", job_id=job_id, reason=type(exc).__name__,
                status=int(getattr(exc, "status", 0) or 0) or None,
                step=getattr(exc, "step", "") or None)
    # Out of retries, so this is a customer waiting for a deliverable that
    # is not coming. A step that failed and was retried never reaches here:
    # the runner raises only once the retries are exhausted.
    alerts.job_failed(
        job_id,
        step=getattr(exc, "step", ""),
        reason=type(exc).__name__,
        attempts=getattr(exc, "attempt", 0),
        status=int(getattr(exc, "status", 0) or 0),
    ).emit()
    workspace.cleanup()
    return "failed"


def _pause(result, workspace, org_id: str, job_id: str,
           request: JobRequest, cap: float) -> str:
    """A job that has stopped for a person, rather than finished or failed.

    The hold stands and nothing is settled: the customer approved a cap for a
    deliverable they have not been given yet, and charging at this point would
    bill them for a transcript while they are still deciding what to keep. The
    money moves when the cut they submit comes back through assembly.

    What IS written is the transcript, the scores and — for a hybrid job — the
    suggested cut, because that is the thing the person is about to edit.

    And if that write does not happen, this is not a pause. `awaiting_edit` is a
    promise that there is something to edit: the editor opens on the job's
    transcript, and with no transcript it opens empty, on a job that says it is
    waiting for the person looking at it. There is no deliverable to weigh
    against here — unlike the completed path, nothing has been produced — so the
    honest end is a failed job with the hold released, which the customer can
    resubmit.
    """
    if not _record_transcript(result, org_id, job_id):
        with session_for_org(org_id) as s:
            job_writes.release(s, org_id, job_id, cap,
                               reason="transcript not recorded")
            _record_cost(s, org_id, job_id, request)
            job_writes.set_status(
                s, org_id, job_id, "failed",
                error={"code": "TranscriptNotRecorded"},
                finished_at=sa.func.now(),
            )
        alerts.job_failed(job_id, step="transcript",
                          reason="TranscriptNotRecorded", attempts=1).emit()
        workspace.cleanup()
        return "failed"

    with session_for_org(org_id) as s:
        _record_cost(s, org_id, job_id, request)
        job_writes.set_status(s, org_id, job_id, "awaiting_edit")
    log.info("job.awaiting_edit", job_id=job_id,
             after=result.paused_after, mode=request.mode)
    # The staged media goes. Stages 0-4 are published to the ingest cache and
    # keyed on content, so the resumed run re-materialises the sources and
    # performs no transcription (ADR-0008, ADR-0016).
    workspace.cleanup()
    return "awaiting_edit"


def _record_transcript(result, org_id: str, job_id: str) -> bool:
    """The beats, the speakers, the scores and the cut, into the read tables.

    Outside the settle transaction, and failing softly, because of what each
    failure costs *on the completed path*. The artifacts are already published
    and validated by then: the customer has a deliverable that opens in their
    NLE. Failing the job over the transcript would release the hold and mark a
    job failed that demonstrably succeeded, and the customer would be looking at
    an error beside four working files.

    So a failure here is loud in the logs and does not touch the money. What it
    costs is the transcript page for that job, which is recoverable by re-running
    it — the ingest cache makes that free of transcription (ADR-0008).

    Returns whether it succeeded, because a job that pauses for a person has no
    deliverable on the other side of the scale — see `_pause`.
    """
    state = result.state
    assets = _asset_ids(state.request.assets)
    try:
        with session_for_org(org_id) as s:
            for ingest in state.assets:
                transcript_writes.record_asset(
                    s, org_id, ingest,
                    asset_id=assets[ingest.asset_id],
                    ingest_version=CACHE_VERSION,
                )
                _record_preview(s, org_id, ingest, assets[ingest.asset_id],
                                state.request.project_id)
            transcript_writes.record_job_view(
                s, org_id, job_id,
                assets=assets,
                candidates=state.candidates,
                scores=state.scores,
                cuts=state.cuts,
            )
    except Exception as exc:  # noqa: BLE001 — see the docstring
        log.error(
            "job.transcript_not_recorded",
            job_id=job_id,
            reason=type(exc).__name__,
        )
        return False
    return True


def _record_cost(s, org_id: str, job_id: str, request: JobRequest) -> int:
    """Project this job's model spend onto its row. Returns cents.

    The per-call rows are already written — the progress sink wrote them as each
    step finished, which is what makes cost visible while a job is still running
    rather than only after it ends. This sums them and records which models
    actually ran, failover included, because a job produced by two vendors was
    produced by both and the reproducibility record has to say so.
    """
    ledger = getattr(request.router, "ledger", None)
    versions = ledger.models_used() if ledger else {}
    return job_writes.set_job_cost(s, org_id, job_id, model_versions=versions)


def _publish(result, workspace, org_id: str, job_id: str, request: JobRequest) -> int:
    """Artifacts to the artifacts bucket, and a row each.

    The deliverable outlives the media it was cut from — the bucket's lifecycle
    says a year against thirty days — so this is the one output that must not be
    left on a worker's disk.
    """
    published = 0
    with session_for_org(org_id) as s:
        for artifact in result.artifacts:
            if not artifact.ok:
                continue
            local = Path(artifact.path)
            ref = workspace.publish_artifact(local, job_id, local.name)
            s.execute(
                sa.insert(m.Artifact.__table__).values(
                    # `kind`, not `fmt`: the identifier, not the label.
                    id=f"art_{job_id}_{artifact.kind}",
                    org_id=org_id,
                    job_id=job_id,
                    kind=artifact.kind,
                    # What the download is called. The key is an id and is not
                    # it — a customer receives `interview_roughcut.aaf`.
                    filename=local.name,
                    s3_key=ref.key if ref else None,
                    bytes=artifact.bytes,
                    # Stage 12 read it back and compared it to the timeline. A
                    # false here is a refund, not a delivery.
                    validated=True,
                )
            )
            published += 1
    return published


def _credits_used(result, tier_id: str) -> float:
    """What the job actually consumed, in credits.

    Source hours at the org's rate, which is what the estimate was priced on.
    The customer is charged for the material they gave us, never for how long
    our machines took — the second number is ours to improve, and billing for
    it would mean a slow release costs them money.
    """
    from ..billing import TIERS

    hours = sum(a.duration_s for a in result.state.assets) / 3600
    return round(hours * TIERS[tier_id].credit_rate_per_source_hour, 2)


def main(argv: list[str] | None = None) -> int:
    # A worker started by hand on a developer's machine reads the same .env the
    # API does. In a deployed container there is no file and the environment is
    # the environment (`config.load_env_file`).
    load_env_file(Path.cwd() / ".env")

    parser = argparse.ArgumentParser(prog="python -m mishne.orchestration.worker")
    parser.add_argument("job_id")
    parser.add_argument("--org", required=True)
    args = parser.parse_args(argv)
    # Tracing is configured once, by the process that owns the job. `run.py` on
    # a laptop never calls this, which is why `telemetry.span` is a no-op until
    # something opts in.
    telemetry.configure(service="mishne-worker")
    status = execute(args.org, args.job_id)
    print(f"{args.job_id}: {status}")
    return 0 if status == "complete" else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())


def _record_preview(s, org_id: str, ingest, asset_id: str, project_id: str) -> None:
    """Point a sequence's asset row at the preview stage 0 rendered.

    Only sequences reach this with anything to record. A flat upload's preview
    is built and recorded by `proxyrunner`, off the pipeline entirely, and its
    row is already `ready` long before a job gets here — hence the guard rather
    than an unconditional write, which would take a finished preview and
    overwrite it with the empty string on every subsequent job over that asset.

    **The key names the content id, not the row id**, because that is where
    `S3Workspace.publish_asset` put the object: the derived cache is
    content-addressed so that the same rushes uploaded to two projects are
    ingested once (ADR-0008), and `ingest.asset_id` is the pipeline's content
    digest rather than `assets.id`.

    Composing the key from the row id instead produces a row that says `ready`,
    a `proxy_s3_key` nothing has ever been written to, and a presigned URL that
    404s — with the real preview sitting in the same bucket under the other
    name. The player reports that as a decode error, so the symptom is an empty
    player rather than anything that mentions a missing object.

    Failing softly for the reason `_record_transcript` does — this runs on the
    completed path, where the artifacts are already published and validated, and
    a preview is not worth failing a job that demonstrably succeeded.
    """
    if not getattr(ingest, "preview_name", ""):
        return
    try:
        uploads.record_proxy(
            s, org_id, asset_id,
            s3_key=derived_key(org_id, project_id, ingest.asset_id,
                               ingest.preview_name),
            kind=ingest.preview_kind,
            size_bytes=ingest.preview_bytes,
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring
        log.warning("job.preview_not_recorded", reason=type(exc).__name__)
