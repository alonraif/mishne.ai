"""Jobs.

Submission is a two-step handshake: the client asks for an estimate, the user
approves it, and the approved cap is sent back with the job. The API recomputes
the estimate server-side and rejects a job whose approved cap does not match —
never trust a client-supplied price.
"""

from pathlib import Path

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit
from ..auth.sessions import Principal
from ..billing import TIERS, estimate_job
from ..db import jobs as job_writes
from ..db import models as m
from ..db import repository
from ..deps import current_principal, require_write, writable_db
from ..logging import get_logger
from ..orchestration import AssetSource, JobRequest, plan as plan_steps
from ..schemas import (
    Artifact,
    CreateJobRequest,
    CreditEstimate,
    EstimateJobRequest,
    Job,
    SubmitCutRequest,
    Transcript,
)
from ..store import Store, get_store

router = APIRouter(prefix="/v1", tags=["jobs"])

log = get_logger(__name__)

#: How far the recomputed estimate may differ from the figure the client says
#: the user approved. Not zero: a price is rounded to two decimals in two
#: places, and refusing a job over a hundredth of a credit is a support ticket.
#: Wide enough to absorb rounding, narrow enough that a stale price is caught.
CAP_TOLERANCE = 0.01


@router.post("/jobs/estimate", response_model=CreditEstimate)
async def estimate(
    body: EstimateJobRequest, store: Store = Depends(get_store)
) -> CreditEstimate:
    """Price a job before submission. Shown to the user for explicit approval.

    Priced on every asset the job will draw on. It used to price the first one
    and so did submission, so the two agreed with each other and both
    under-charged a multi-source job — the error was invisible precisely
    because it was made consistently in two places.
    """
    assets = [store.get_asset(a) for a in body.assets]
    missing = [a for a, row in zip(body.assets, assets) if row is None]
    if missing:
        raise HTTPException(404, f"no such asset: {missing[0]}")
    org = store.get_org()
    if org is None:
        raise HTTPException(404, "organisation not found")
    tier = TIERS[org.tier]
    total_seconds = sum(
        a.duration_frames * a.rate.den / a.rate.num for a in assets
    )
    if total_seconds > tier.max_source_hours * 3600:
        raise HTTPException(
            422,
            f"{total_seconds / 3600:.1f} source hours exceeds the "
            f"{tier.max_source_hours}-hour limit on the {tier.name} plan",
        )
    return estimate_job(assets, tier, org.credit_balance, body.mode)


@router.post("/jobs", response_model=Job, status_code=202)
async def create_job(
    body: CreateJobRequest,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
) -> Job:
    """Accept a job.

    Recompute the estimate, verify the client's approved cap matches, place a
    hold for that amount, then write the steps the workflow will run. A hold at
    submission — rather than a debit at completion — is what stops a user with
    five credits starting ten concurrent jobs (ADR-0006).

    **The price is recomputed here and never trusted from the request.** The
    client sends what the user approved so that the two can be compared; a job
    whose price has moved since the estimate was shown is refused rather than
    quietly charged at the new one.
    """
    org_id = principal.org_id
    if not body.asset_ids:
        raise HTTPException(422, "a job needs at least one asset")

    assets = [repository.get_asset(s, org_id, a) for a in body.asset_ids]
    missing = [a for a, row in zip(body.asset_ids, assets) if row is None]
    if missing:
        raise HTTPException(404, f"no such asset: {missing[0]}")
    not_ready = [a.id for a in assets if a.status != "ready"]
    if not_ready:
        # An `awaiting_media` sequence would transcribe silence; a `probing`
        # asset has no duration yet and therefore no price.
        raise HTTPException(
            409, f"asset {not_ready[0]} is not ready to cut"
        )

    org = repository.get_org(s, org_id)
    if org is None:  # pragma: no cover - the session just proved it exists
        raise HTTPException(404, "organisation not found")
    tier = TIERS[org.tier]

    total_hours = sum(
        a.duration_frames * a.rate.den / a.rate.num / 3600 for a in assets
    )
    if total_hours > tier.max_source_hours:
        raise HTTPException(
            422,
            f"{total_hours:.1f} source hours exceeds the "
            f"{tier.max_source_hours}-hour limit on the {tier.name} plan",
        )

    # Every asset, matching what `estimate` showed the user. Both used to price
    # `assets[0]`, which is why the mismatch never tripped the cap check.
    estimate = estimate_job(assets, tier, org.credit_balance, body.mode)
    if abs(estimate.cap - body.approved_cap) > CAP_TOLERANCE:
        raise HTTPException(
            409,
            f"the price has changed since you approved it: "
            f"{estimate.cap} credits, not {body.approved_cap}",
        )

    brief = {
        "target_duration_s": body.target_duration_s,
        "narrative_shape": body.narrative_shape,
        "tone": body.tone,
        "handle_frames": 0,
    }
    job_id = job_writes.create_job(
        s, org_id,
        project_id=assets[0].project_id,
        asset_ids=body.asset_ids,
        mode=body.mode,
        notes=body.notes,
        brief=brief,
        estimate=estimate.model_dump(),
        approved_cap=estimate.cap,
    )
    try:
        job_writes.hold(s, org_id, job_id, assets[0].project_id, estimate.cap)
    except job_writes.InsufficientCredits as exc:
        raise HTTPException(
            402,
            f"this job needs {exc.required} credits and {exc.available} are available",
        ) from exc

    # The steps this job will run, written now so a queued job shows its shape
    # rather than an empty panel.
    job_writes.plan_steps(
        s, org_id, job_id,
        plan_steps(
            JobRequest(
                job_id=job_id, org_id=org_id, project_id=assets[0].project_id,
                assets=[
                    AssetSource(asset_id=a.id, path=Path(a.filename)) for a in assets
                ],
                out_dir=Path("."), work_dir=Path("."),
            )
        ),
    )
    audit.record(
        s, org_id, audit.JOB_CREATED, resource_type="job", resource_id=job_id,
        actor_user_id=principal.user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    log.info("job.accepted", job_id=job_id, assets=len(assets),
             cap=float(estimate.cap), mode=body.mode)

    job = repository.get_job(s, org_id, job_id)
    if job is None:  # pragma: no cover
        raise HTTPException(500, "the job was accepted but could not be read back")
    return job


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, store: Store = Depends(get_store)) -> Job:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(
    job_id: str,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
) -> Job:
    """Cancel, and release the whole hold.

    Cancellation is a row, not a signal: the status is set here and the worker
    notices between steps. No inter-process signalling, and no stage is killed
    part-way through — a stage is at most a few minutes, and interrupting one
    mid-write is how a half-written artifact reaches a customer.
    """
    org_id = principal.org_id
    job = repository.get_job(s, org_id, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.status in ("complete", "failed", "cancelled"):
        raise HTTPException(409, f"this job is already {job.status}")

    job_writes.set_status(s, org_id, job_id, "cancelled")
    # Released now rather than when the worker notices: the user asked for their
    # credits back, and a hold that outlives the job by however long a stage
    # takes is a balance that looks wrong for no reason. The worker's own
    # release is idempotent (ADR-0006).
    job_writes.release(
        s, org_id, job_id, float(job.estimate.cap if job.estimate else 0),
        reason="job cancelled",
    )
    log.info("job.cancelled", job_id=job_id)
    cancelled = repository.get_job(s, org_id, job_id)
    if cancelled is None:  # pragma: no cover
        raise HTTPException(404, "job not found")
    return cancelled


@router.get("/jobs/{job_id}/artifacts", response_model=list[Artifact])
async def get_artifacts(job_id: str, store: Store = Depends(get_store)) -> list[Artifact]:
    return store.list_artifacts(job_id)


@router.get("/jobs/{job_id}/transcript", response_model=Transcript)
async def get_transcript(job_id: str, store: Store = Depends(get_store)) -> Transcript:
    transcript = store.get_transcript(job_id)
    if transcript is None:
        raise HTTPException(404, "transcript not found")
    return transcript


@router.post("/jobs/{job_id}/cut", response_model=Job)
async def submit_cut(
    job_id: str,
    body: SubmitCutRequest,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
) -> Job:
    """Submit a user-authored cut for manual or hybrid jobs.

    The ordered beat ids stand in for the solver's output. Stages 9-12 — refine
    cut points, assemble, emit, validate — run exactly as they would for an AI
    job, which is why text-based editing costs almost nothing to support
    (ADR-0007).

    The rows written here are the person's *intention*, at the beats' own
    boundaries. The worker rewrites them once stage 9 has snapped those
    boundaries to real silence and added handles, so what ends up stored is
    where the cut actually is rather than where it was asked for. Both are the
    same list in the same order; only the frames move.

    No money moves here. The hold placed at submission still stands, and the
    job settles when the cut it produces is delivered.
    """
    org_id = principal.org_id
    job = repository.get_job(s, org_id, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    if job.mode == "ai":
        raise HTTPException(
            409,
            "this job chose its own cut. Start a hybrid job to edit a "
            "suggestion, or a manual one to mark the transcript yourself.",
        )
    if job.status != "awaiting_edit":
        # Accepting a cut for a running job would race the worker for the same
        # rows; accepting one for a finished job would silently not re-render.
        raise HTTPException(
            409, f"this job is {job.status}, not waiting for an edit"
        )
    if not body.beat_ids:
        raise HTTPException(422, "a cut needs at least one beat")
    if len(set(body.beat_ids)) != len(body.beat_ids):
        # Ordered, so a repeat is a distinct position, and `selections` would
        # take it. Refused because it is far more likely to be a double-click in
        # the editor than a deliberate reuse of one line twice in a piece.
        raise HTTPException(422, "the same beat appears twice in this cut")

    beats = m.Beat.__table__
    rows = s.execute(
        sa.select(beats.c.id, beats.c.asset_id, beats.c.start_frames,
                  beats.c.end_frames)
        .where(beats.c.org_id == org_id, beats.c.id.in_(body.beat_ids))
    ).all()
    found = {r.id: r for r in rows}
    unknown = [b for b in body.beat_ids if b not in found]
    if unknown:
        raise HTTPException(422, f"no such beat in this job: {unknown[0]}")
    wrong_job = [r.id for r in rows if r.asset_id not in job.asset_ids]
    if wrong_job:
        # A beat of another job's material, which is a different cut entirely
        # and would assemble against media this job never staged.
        raise HTTPException(
            422, f"beat {wrong_job[0]} is not from this job's uploads"
        )

    job_writes.replace_cut(
        s, org_id, job_id,
        [(found[b].asset_id, b, found[b].start_frames, found[b].end_frames)
         for b in body.beat_ids],
    )
    # Back in the queue. A worker picks it up, finds the stored cut, and runs
    # from stage 9 — the per-asset stages come from the ingest cache and cost
    # nothing (ADR-0008, ADR-0016).
    job_writes.set_status(s, org_id, job_id, "queued")
    audit.record(
        s, org_id, audit.JOB_CUT_SUBMITTED, resource_type="job",
        resource_id=job_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    log.info("job.cut_submitted", job_id=job_id, beats=len(body.beat_ids),
             mode=job.mode)

    updated = repository.get_job(s, org_id, job_id)
    if updated is None:  # pragma: no cover
        raise HTTPException(404, "job not found")
    return updated
