"""Jobs.

Submission is a two-step handshake: the client asks for an estimate, the user
approves it, and the approved cap is sent back with the job. The API recomputes
the estimate server-side and rejects a job whose approved cap does not match —
never trust a client-supplied price.
"""

from fastapi import APIRouter, HTTPException

from .. import mock
from ..billing import TIERS, estimate_job
from ..schemas import (
    Artifact,
    CreateJobRequest,
    CreditEstimate,
    EstimateJobRequest,
    Job,
    SubmitCutRequest,
    Transcript,
)

router = APIRouter(prefix="/v1", tags=["jobs"])


@router.post("/jobs/estimate", response_model=CreditEstimate)
async def estimate(body: EstimateJobRequest) -> CreditEstimate:
    """Price a job before submission. Shown to the user for explicit approval."""
    asset = next((a for a in mock.ASSETS if a.id == body.asset_id), None)
    if asset is None:
        raise HTTPException(404, "asset not found")
    tier = TIERS[mock.ORG.tier]
    if asset.duration_frames * asset.rate.den / asset.rate.num > tier.max_source_hours * 3600:
        raise HTTPException(
            422,
            f"source exceeds the {tier.max_source_hours}-hour limit on the {tier.name} plan",
        )
    return estimate_job(asset, tier, mock.ORG.credit_balance, body.mode)


@router.post("/jobs", response_model=Job, status_code=202)
async def create_job(body: CreateJobRequest) -> Job:
    """Accept a job.

    Recompute the estimate, verify the client's approved_cap matches, place a
    hold for that amount, then start the workflow. A hold at submission — rather
    than a debit at completion — is what stops a user with five credits from
    starting ten concurrent jobs.
    """
    raise HTTPException(501, "not implemented")


@router.get("/jobs/{job_id}", response_model=Job)
async def get_job(job_id: str) -> Job:
    for j in mock.JOBS:
        if j.id == job_id:
            return j
    raise HTTPException(404, "job not found")


@router.post("/jobs/{job_id}/cancel", response_model=Job)
async def cancel_job(job_id: str) -> Job:
    """Cancel and release the whole hold."""
    raise HTTPException(501, "not implemented")


@router.get("/jobs/{job_id}/artifacts", response_model=list[Artifact])
async def get_artifacts(job_id: str) -> list[Artifact]:
    return [a for a in mock.ARTIFACTS if a.job_id == job_id]


@router.get("/jobs/{job_id}/transcript", response_model=Transcript)
async def get_transcript(job_id: str) -> Transcript:
    raise HTTPException(501, "not implemented")


@router.post("/jobs/{job_id}/cut", response_model=Job)
async def submit_cut(job_id: str, body: SubmitCutRequest) -> Job:
    """Submit a user-authored cut for manual or hybrid jobs.

    The ordered beat ids stand in for the solver's output. Stages 9-12 —
    refine cut points, assemble, emit, validate — run exactly as they would for
    an AI job, which is why text-based editing costs almost nothing to support.
    """
    raise HTTPException(501, "not implemented")
