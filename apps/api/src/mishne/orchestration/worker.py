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
from ..config import Settings, get_settings
from ..db import jobs as job_writes
from ..db import models as m
from ..db.base import session_for_org
from ..llm import Router
from ..logging import get_logger
from ..storage import ObjectRef
from ..workspace import S3Workspace, SourceFile
from .graph import AssetSource, JobRequest
from .runner import Cancelled, run_job
from .sink import DatabaseSink

log = get_logger(__name__)


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
                # The pipeline's id is the content hash, not the row id: the
                # same rushes in two projects are two rows and one ingest.
                content_id=f"a_{row.checksum[:24]}" if row.checksum else "",
            )
        )

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
        handle_frames=brief.get("handle_frames", 6),
        language=brief.get("language"),
        stem=f"{rows[0].filename.rsplit('.', 1)[0]}_roughcut",
        router=Router(),
    )
    return request, {"approved_cap": approved_cap, "workspace": workspace,
                     "tier": tier}


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
        with session_for_org(org_id) as s:
            job_writes.release(s, org_id, job_id, cap, reason="job failed")
            # A failed job costs the customer nothing and costs us whatever it
            # spent before it died. Recording only successful jobs' costs is
            # how a retry storm stays invisible in the cost model.
            _record_cost(s, org_id, job_id, request)
            job_writes.set_status(
                s, org_id, job_id, "failed",
                # The type, not the message: an exception can quote a filename.
                error={"code": type(exc).__name__},
                finished_at=sa.func.now(),
            )
        log.warning("job.failed", job_id=job_id, reason=type(exc).__name__)
        # Out of retries, so this is a customer waiting for a deliverable that
        # is not coming. A step that failed and was retried never reaches here:
        # the runner raises only once the retries are exhausted.
        alerts.job_failed(
            job_id,
            step=getattr(exc, "step", ""),
            reason=type(exc).__name__,
            attempts=getattr(exc, "attempt", 0),
        ).emit()
        workspace.cleanup()
        return "failed"

    published = _publish(result, workspace, org_id, job_id, request)
    actual = _credits_used(result, meta["tier"])
    with session_for_org(org_id) as s:
        charged = job_writes.settle(s, org_id, job_id, actual, cap)
        cost_cents = _record_cost(s, org_id, job_id, request)
        job_writes.set_status(
            s, org_id, job_id, "complete", finished_at=sa.func.now()
        )
    log.info("job.settled", job_id=job_id, charged=charged, artifacts=published,
             model_cost_cents=cost_cents)
    with session_for_org(org_id) as s:
        moved = alerts.spend_moved(s, org_id, job_id, cost_cents=cost_cents)
    if moved:
        moved.emit()
    workspace.cleanup()
    return "complete"


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
                    id=f"art_{job_id}_{artifact.fmt}",
                    org_id=org_id,
                    job_id=job_id,
                    kind=artifact.fmt,
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
