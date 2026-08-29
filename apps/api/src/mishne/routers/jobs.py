"""Jobs.

Submission is a two-step handshake: the client asks for an estimate, the user
approves it, and the approved cap is sent back with the job. The API recomputes
the estimate server-side and rejects a job whose approved cap does not match —
never trust a client-supplied price.
"""

from fastapi import APIRouter, Depends, HTTPException

from ..auth.sessions import Principal
from ..billing import TIERS, estimate_job
from ..deps import require_write
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


@router.post("/jobs/estimate", response_model=CreditEstimate)
async def estimate(
    body: EstimateJobRequest, store: Store = Depends(get_store)
) -> CreditEstimate:
    """Price a job before submission. Shown to the user for explicit approval.

    Still single-asset, while `Job.asset_ids` is not: a multi-asset job has to
    be priced on the sum of its sources, and that is a billing change rather
    than a persistence one. Tracked, not fixed here.
    """
    asset = store.get_asset(body.asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")
    org = store.get_org()
    if org is None:
        raise HTTPException(404, "organisation not found")
    tier = TIERS[org.tier]
    if asset.duration_frames * asset.rate.den / asset.rate.num > tier.max_source_hours * 3600:
        raise HTTPException(
            422,
            f"source exceeds the {tier.max_source_hours}-hour limit on the {tier.name} plan",
        )
    return estimate_job(asset, tier, org.credit_balance, body.mode)


@router.post("/jobs", response_model=Job, status_code=202)
async def create_job(
    body: CreateJobRequest, _: Principal = Depends(require_write)
) -> Job:
    """Accept a job.

    Recompute the estimate, verify the client's approved_cap matches, place a
    hold for that amount, then start the workflow. A hold at submission — rather
    than a debit at completion — is what stops a user with five credits from
    starting ten concurrent jobs.
    """
    raise HTTPException(501, "not implemented")


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str, store: Store = Depends(get_store)) -> Job:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str, _: Principal = Depends(require_write)) -> Job:
    """Cancel and release the whole hold."""
    raise HTTPException(501, "not implemented")


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
    job_id: str, body: SubmitCutRequest, _: Principal = Depends(require_write)
) -> Job:
    """Submit a user-authored cut for manual or hybrid jobs.

    The ordered beat ids stand in for the solver's output. Stages 9-12 —
    refine cut points, assemble, emit, validate — run exactly as they would for
    an AI job, which is why text-based editing costs almost nothing to support.
    """
    raise HTTPException(501, "not implemented")
