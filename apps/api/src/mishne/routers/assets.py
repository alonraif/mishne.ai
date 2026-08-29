"""Asset upload.

Media never transits the API. The browser gets presigned multipart URLs and
talks to S3 directly — proxying a 200 GB ProRes master through the application
tier is both an enormous bandwidth bill and a guaranteed source of timeouts.

Everything the client sends here is a claim, and the two that matter are
recomputed rather than trusted:

* **the key**, which is derived from the org, the project and the content hash,
  so a caller cannot name the object it is about to write and cannot write over
  somebody else's; and
* **the part layout**, which follows from the declared size, so a client cannot
  ask for ten thousand presigned URLs by claiming a petabyte.

The declared size and checksum are still claims — S3 enforces neither at
CompleteMultipartUpload — which is why probe, not this module, is what moves an
asset to `ready`. An upload that completes is an object of unknown provenance
until stage 0 has read it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from .. import audit, storage
from ..config import Settings, get_settings
from ..db import repository, requirements as reqs, uploads
from ..auth.sessions import Principal
from ..deps import current_principal, require_write, writable_db
from ..logging import get_logger
from ..schemas import (
    Asset,
    AssetRequirements,
    CompleteUploadRequest,
    CreateAssetRequest,
    MediaRequirement,
    PresignedUpload,
    ResumeUploadRequest,
    UploadedPart,
    UploadPart,
    UploadState,
)
from ..store import Store, get_store

router = APIRouter(prefix="/v1", tags=["assets"])

log = get_logger(__name__)

#: A sha-256 digest, lower-case hex. The browser computes it while reading the
#: file to upload it, so requiring it costs the client nothing it was not doing.
_HEX = set("0123456789abcdef")


def _checksum(raw: str) -> str:
    value = raw.strip().lower()
    if len(value) != 64 or not set(value) <= _HEX:
        raise HTTPException(422, "checksum must be a hex sha-256 digest")
    return value


def _presign(
    store: storage.Storage,
    ref: storage.ObjectRef,
    *,
    asset_id: str,
    upload_id: str,
    total_bytes: int,
    part_size: int,
    wanted: list[int] | None,
    ttl: int,
) -> PresignedUpload:
    """The response, minted for every part or only the ones asked for."""
    total = storage.part_count(total_bytes, part_size)
    parts = store.part_urls(ref, upload_id, total_bytes, part_size)
    if wanted:
        unknown = [n for n in wanted if n < 1 or n > total]
        if unknown:
            raise HTTPException(422, f"no such part: {sorted(unknown)[:5]}")
        keep = set(wanted)
        parts = [p for p in parts if p.part_number in keep]
    return PresignedUpload(
        asset_id=asset_id,
        upload_id=upload_id,
        part_size=part_size,
        total_parts=total,
        parts=[
            UploadPart(
                part_number=p.part_number, url=p.url, offset=p.offset, length=p.length
            )
            for p in parts
        ],
        expires_in_s=ttl,
    )


@router.post("/projects/{project_id}/assets", response_model=PresignedUpload, status_code=201)
async def create_asset(
    project_id: str,
    body: CreateAssetRequest,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> PresignedUpload:
    """Authorize, enforce the size ceiling, and hand back presigned part URLs.

    Idempotent in the way that matters: the asset id is derived from the project
    and the content hash, so a client that retries after a dropped response gets
    the same row and the same key rather than a second orphaned upload paying
    for parts nobody will complete.
    """
    org_id = principal.org_id
    checksum = _checksum(body.checksum)
    if body.bytes < 0:
        raise HTTPException(422, "bytes must not be negative")
    if body.bytes > settings.max_upload_bytes:
        raise HTTPException(
            413,
            f"upload exceeds the {settings.max_upload_bytes // 1024**3} GiB limit",
        )
    if not uploads.project_exists(s, org_id, project_id):
        raise HTTPException(404, "project not found")

    kind, ingest_mode = uploads.kind_and_mode(body.filename)
    declared_rate: tuple[int, int] | None = None
    if kind == "audio":
        # Audio carries no frame rate. Probe cannot find one, and a guess is a
        # cut that is a frame out everywhere (ADR-0005), so it is asked for
        # here — the one moment the caller definitely knows it.
        if body.rate is None or body.rate.num <= 0 or body.rate.den <= 0:
            raise HTTPException(
                422,
                "an audio-only upload must declare the sequence rate: "
                "there is none in the file",
            )
        declared_rate = (body.rate.num, body.rate.den)

    asset_id = uploads.asset_row_id(project_id, checksum)
    existing = uploads.get_row(s, org_id, asset_id)
    if existing is not None and existing.status != "uploading":
        raise HTTPException(
            409,
            "this file is already uploaded to this project",
            headers={"X-Asset-Id": asset_id},
        )

    obj = storage.Storage(settings)
    ref = storage.ObjectRef(
        bucket=storage.bucket_for("raw", settings),
        key=storage.source_key(org_id, project_id, asset_id),
    )
    part_size = storage.choose_part_size(body.bytes)

    if existing is not None:
        # A retry, or a browser coming back to a file it was part-way through.
        # The existing multipart upload is KEPT if S3 still has it: the parts
        # already sent are hours of somebody's evening, and starting again
        # because the page was refreshed is the difference between a resumable
        # upload and one that merely says it is. `GET /upload-parts` is how the
        # client finds out what survived.
        upload_id = ""
        if existing.upload_id:
            try:
                obj.list_parts(ref, existing.upload_id)
                upload_id = existing.upload_id
            except Exception:  # noqa: BLE001 - gone, expired, or aborted
                log.info("stale_upload_discarded", asset_id=asset_id)
                try:
                    obj.abort_multipart(ref, existing.upload_id)
                except Exception:  # noqa: BLE001 - already gone is the goal
                    pass
        if not upload_id:
            upload_id = obj.initiate_multipart(ref)
            uploads.restart_upload(s, org_id, asset_id, upload_id)
    else:
        upload_id = obj.initiate_multipart(ref)
        uploads.create_asset(
            s,
            org_id,
            project_id=project_id,
            asset_id=asset_id,
            filename=body.filename,
            size_bytes=body.bytes,
            checksum=checksum,
            kind=kind,
            ingest_mode=ingest_mode,
            bucket=ref.bucket,
            key=ref.key,
            upload_id=upload_id,
            rate=declared_rate,
        )

    log.info(
        "upload_initiated",
        asset_id=asset_id,
        project_id=project_id,
        bytes=body.bytes,
        parts=storage.part_count(body.bytes, part_size),
    )
    audit.record(
        s, org_id, audit.UPLOAD_STARTED, resource_type="asset", resource_id=asset_id,
        actor_user_id=principal.user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return _presign(
        obj,
        ref,
        asset_id=asset_id,
        upload_id=upload_id,
        total_bytes=body.bytes,
        part_size=part_size,
        wanted=None,
        ttl=settings.presign_ttl_seconds,
    )


@router.post("/assets/{asset_id}/upload-urls", response_model=PresignedUpload)
async def resume_upload(
    asset_id: str,
    body: ResumeUploadRequest,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> PresignedUpload:
    """Fresh URLs for an upload already in flight.

    The TTL is 900 seconds and a large file takes longer than that, so this is
    not an error path — it is the ordinary middle of a big upload, and the same
    endpoint serves a resume after a network drop or a closed laptop. The part
    layout is recomputed from the stored size, so the parts a client already
    sent are still exactly the parts it thinks it sent.
    """
    org_id = principal.org_id
    row = uploads.get_row(s, org_id, asset_id)
    if row is None:
        raise HTTPException(404, "asset not found")
    if row.status != "uploading" or not row.upload_id:
        raise HTTPException(409, f"asset is {row.status}; there is no upload in flight")
    return _presign(
        storage.Storage(settings),
        storage.ObjectRef(bucket=row.s3_bucket, key=row.s3_key),
        asset_id=asset_id,
        upload_id=row.upload_id,
        total_bytes=row.bytes,
        part_size=storage.choose_part_size(row.bytes),
        wanted=body.part_numbers or None,
        ttl=settings.presign_ttl_seconds,
    )


@router.get("/assets/{asset_id}/upload-parts", response_model=UploadState)
async def upload_parts(
    asset_id: str,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> UploadState:
    """What S3 already has. The other half of resuming.

    A browser that was closed mid-upload knows nothing when it comes back, and a
    presigned URL grants one operation on one object — so it cannot ask S3 what
    it managed to send. This is the API asking on its behalf, and it is what
    makes a resume cost only the parts that are actually missing.
    """
    org_id = principal.org_id
    row = uploads.get_row(s, org_id, asset_id)
    if row is None:
        raise HTTPException(404, "asset not found")
    if row.status != "uploading" or not row.upload_id:
        raise HTTPException(409, f"asset is {row.status}; there is no upload in flight")
    part_size = storage.choose_part_size(row.bytes)
    held = storage.Storage(settings).list_parts(
        storage.ObjectRef(bucket=row.s3_bucket, key=row.s3_key), row.upload_id
    )
    return UploadState(
        asset_id=asset_id,
        upload_id=row.upload_id,
        part_size=part_size,
        total_parts=storage.part_count(row.bytes, part_size),
        total_bytes=row.bytes,
        uploaded=[
            UploadedPart(part_number=n, etag=tag, size=size) for n, tag, size in held
        ],
    )


@router.post("/assets/{asset_id}/complete", response_model=Asset)
async def complete_upload(
    asset_id: str,
    body: CompleteUploadRequest,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> Asset:
    """Assemble the parts, then move the asset to `probing`.

    Not to `ready`. The object exists; what is in it is still a claim until
    stage 0 has read it, and an asset that says `ready` is one a job may be
    started against.
    """
    org_id = principal.org_id
    row = uploads.get_row(s, org_id, asset_id)
    if row is None:
        raise HTTPException(404, "asset not found")
    if row.status != "uploading" or not row.upload_id:
        raise HTTPException(409, f"asset is {row.status}; there is no upload to complete")

    expected = storage.part_count(row.bytes, storage.choose_part_size(row.bytes))
    if len(body.parts) != expected:
        raise HTTPException(422, f"expected {expected} parts, got {len(body.parts)}")
    numbers = sorted(p.part_number for p in body.parts)
    if numbers != list(range(1, expected + 1)):
        raise HTTPException(422, "part numbers must be 1..n with no gaps or repeats")

    ref = storage.ObjectRef(bucket=row.s3_bucket, key=row.s3_key)
    try:
        storage.Storage(settings).complete_multipart(
            ref, row.upload_id, [(p.part_number, p.etag) for p in body.parts]
        )
    except Exception:  # noqa: BLE001 - S3's message may quote the key
        # The row keeps its upload id: the parts are still there and the client
        # can retry the completion, or abort and start again.
        log.warning("upload_complete_failed", asset_id=asset_id, parts=expected)
        raise HTTPException(502, "could not complete the upload; retry or cancel it")

    uploads.mark_uploaded(s, org_id, asset_id)

    # A file just landed: which sequences in this org were waiting for it? This
    # is one indexed lookup, not media work, so it belongs on this request —
    # the alternative is a customer who has uploaded everything and still sees
    # "waiting for media" until something else happens to run.
    for waiting in reqs.satisfy(s, org_id, asset_id, row.filename):
        reqs.refresh_status(s, org_id, waiting)

    log.info("upload_completed", asset_id=asset_id, bytes=row.bytes, parts=expected)
    audit.record(
        s, org_id, audit.UPLOAD_COMPLETED, resource_type="asset", resource_id=asset_id,
        actor_user_id=principal.user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    asset = repository.get_asset(s, org_id, asset_id)
    if asset is None:  # pragma: no cover - the row was read two statements ago
        raise HTTPException(404, "asset not found")
    return asset


@router.delete("/assets/{asset_id}/upload", status_code=204)
async def abort_upload(
    asset_id: str,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Cancel an upload in flight and stop paying for its parts.

    The lifecycle rule that reaps incomplete multipart uploads is the backstop
    for the ones nobody cancels; this is the one the user cancelled, and it
    should not wait seven days.
    """
    org_id = principal.org_id
    row = uploads.get_row(s, org_id, asset_id)
    if row is None:
        raise HTTPException(404, "asset not found")
    if row.status != "uploading":
        raise HTTPException(409, f"asset is {row.status}; there is no upload in flight")
    if row.upload_id:
        try:
            storage.Storage(settings).abort_multipart(
                storage.ObjectRef(bucket=row.s3_bucket, key=row.s3_key), row.upload_id
            )
        except Exception:  # noqa: BLE001 - already gone is the desired state
            log.info("abort_upload_failed", asset_id=asset_id)
    uploads.delete_asset(s, org_id, asset_id)
    audit.record(
        s, org_id, audit.UPLOAD_CANCELLED, resource_type="asset", resource_id=asset_id,
        actor_user_id=principal.user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    log.info("upload_aborted", asset_id=asset_id)
    return Response(status_code=204)


@router.get("/assets/{asset_id}/requirements", response_model=AssetRequirements)
async def asset_requirements(
    asset_id: str,
    principal: Principal = Depends(current_principal),
    s: Session = Depends(writable_db),
) -> AssetRequirements:
    """What this sequence is still waiting for, and what has arrived.

    A read, on the writable session, because it is only meaningful against real
    rows: a linked AAF is a thing that exists after a probe, and there is no
    fixture for it.
    """
    org_id = principal.org_id
    row = uploads.get_row(s, org_id, asset_id)
    if row is None:
        raise HTTPException(404, "asset not found")
    rows = reqs.for_asset(s, org_id, asset_id)
    return AssetRequirements(
        asset_id=asset_id,
        status=row.status,
        outstanding=sum(1 for r in rows if r.satisfied_by_asset_id is None),
        requirements=[
            MediaRequirement(
                basename=r.basename,
                clip_count=r.clip_count,
                satisfied=r.satisfied_by_asset_id is not None,
                satisfied_by_asset_id=r.satisfied_by_asset_id,
            )
            for r in rows
        ],
    )


@router.get("/assets/{asset_id}", response_model=Asset)
async def get_asset(asset_id: str, store: Store = Depends(get_store)) -> Asset:
    asset = store.get_asset(asset_id)
    if asset is None:
        raise HTTPException(404, "asset not found")
    return asset
