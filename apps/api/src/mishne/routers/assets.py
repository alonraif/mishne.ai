"""Asset upload.

Media never transits the API. The browser gets presigned multipart URLs and
talks to S3 directly — proxying a 200 GB ProRes master through the application
tier is both an enormous bandwidth bill and a guaranteed source of timeouts.
"""

from fastapi import APIRouter, HTTPException

from .. import mock
from ..schemas import Asset, CompleteUploadRequest, CreateAssetRequest, PresignedUpload

router = APIRouter(prefix="/v1", tags=["assets"])


@router.post("/projects/{project_id}/assets", response_model=PresignedUpload, status_code=201)
async def create_asset(project_id: str, body: CreateAssetRequest) -> PresignedUpload:
    """Authorize, enforce quota, and hand back presigned multipart part URLs."""
    raise HTTPException(501, "not implemented")


@router.post("/assets/{asset_id}/complete", response_model=Asset)
async def complete_upload(asset_id: str, body: CompleteUploadRequest) -> Asset:
    raise HTTPException(501, "not implemented")


@router.get("/assets/{asset_id}", response_model=Asset)
async def get_asset(asset_id: str) -> Asset:
    for a in mock.ASSETS:
        if a.id == asset_id:
            return a
    raise HTTPException(404, "asset not found")
