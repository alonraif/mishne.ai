"""The upload lifecycle, as database writes.

`repository.py` is the read half and says so in its docstring; this is the write
half, kept separate because the argument for its correctness is a different one.
A read is wrong if it returns the wrong rows. A write here is wrong if it leaves
the *object store* and the *database* disagreeing — a row that says `ready`
pointing at a key that holds three of nine parts, or a multipart upload nobody
will ever complete and everybody keeps paying for.

Two rules follow from that, and every function here obeys them:

**The row is written before the upload is initiated, and the upload is completed
before the row says so.** Ordered that way, the failure modes are an asset row
with no object (visible, harmless, cleaned up by the abort path) rather than an
object with no row (invisible, billed, and impossible to attribute to a tenant).

**An asset id is derived from the project and the content**, so a client that
retries `create_asset` after a dropped connection gets the same row and the same
key back instead of a second orphaned upload. `storage.source_key` promises the
key is deterministic; this is what makes that true.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from . import models as m

#: Extensions that mean "no picture" — the ADR-0005 path, where the edit rate
#: cannot be probed from the file and has to be supplied.
AUDIO_SUFFIXES = {
    ".wav", ".mp3", ".m4a", ".aac", ".flac", ".aif", ".aiff", ".ogg", ".opus", ".caf",
}


def asset_row_id(project_id: str, checksum: str) -> str:
    """The row id for a piece of content in a project. Deterministic.

    Not `storage.content_id`, deliberately. That is the id of the *content*, and
    it is what keys the ingest cache so the same rushes are transcribed once
    however many projects use them. This is the id of a *row*, and two projects
    holding the same footage are two rows — migration 0002 says so, and the
    `(org_id, checksum)` index it adds is how the earlier one is found.

    Hashed rather than concatenated so the id is a fixed length whatever the
    project id looks like, and so it carries no substring anybody will be
    tempted to parse.
    """
    digest = hashlib.sha256(f"{project_id}:{checksum.lower()}".encode()).hexdigest()
    return f"ast_{digest[:16]}"


def kind_and_mode(filename: str) -> tuple[str, str]:
    """What kind of upload this is, decided from the name — server-side.

    The client sends `ingest_mode` in the request and it is not trusted: it
    decides whether the pipeline will look for companion media, which is a
    thing a caller should not be able to assert about somebody else's project.

    An AAF is recorded as `aaf_embedded` provisionally. Nothing short of parsing
    the file can tell embedded essence from a few hundred kilobytes of pointers,
    and that is stage 0's job; probe corrects it to `aaf_linked` and moves the
    asset to `awaiting_media` when the clips do not resolve.
    """
    lowered = filename.lower()
    dot = lowered.rfind(".")
    suffix = lowered[dot:] if dot > 0 else ""
    if suffix == ".aaf":
        return "aaf", "aaf_embedded"
    if suffix in AUDIO_SUFFIXES:
        return "audio", "audio_only"
    return "video", "full_media"


#: The rate an asset carries between `create` and `probe`. One frame per second
#: is not a rate any camera or sequence has ever had, which is the point: the
#: columns are `NOT NULL` and nothing knows the real value until stage 0 runs,
#: so the placeholder is chosen to be conspicuous in a UI and absurd in
#: arithmetic rather than plausible in both. `probed_at IS NULL` is the honest
#: signal; this is what makes a leak of it obvious.
UNPROBED_RATE = (1, 1)


def get_row(s: Session, org_id: str, asset_id: str) -> sa.Row | None:
    a = m.Asset.__table__
    return s.execute(sa.select(a).where(a.c.org_id == org_id, a.c.id == asset_id)).first()


def find_by_checksum(s: Session, org_id: str, checksum: str) -> sa.Row | None:
    """An earlier upload of the same bytes, anywhere in this org.

    Uses `ix_assets_org_checksum`. Nothing here deduplicates on it — two
    projects are two rows — but a caller that wants to say "you already have
    this" or to skip re-transcription has one lookup instead of a scan.
    """
    a = m.Asset.__table__
    return s.execute(
        sa.select(a)
        .where(a.c.org_id == org_id, a.c.checksum == checksum, a.c.status == "ready")
        .order_by(a.c.created_at)
        .limit(1)
    ).first()


def project_exists(s: Session, org_id: str, project_id: str) -> bool:
    p = m.Project.__table__
    return s.execute(
        sa.select(sa.literal(1)).where(p.c.org_id == org_id, p.c.id == project_id)
    ).first() is not None


def create_asset(
    s: Session,
    org_id: str,
    *,
    project_id: str,
    asset_id: str,
    filename: str,
    size_bytes: int,
    checksum: str,
    kind: str,
    ingest_mode: str,
    bucket: str,
    key: str,
    upload_id: str,
    rate: tuple[int, int] | None = None,
) -> None:
    """The row for an upload that is about to start.

    `rate` is the declared sequence rate for an audio-only upload, which is the
    one case where the file cannot tell us and the caller must (ADR-0005).
    Everything else gets the placeholder until probe runs.
    """
    a = m.Asset.__table__
    num, den = rate or UNPROBED_RATE
    s.execute(
        sa.insert(a).values(
            id=asset_id,
            org_id=org_id,
            project_id=project_id,
            kind=kind,
            ingest_mode=ingest_mode,
            status="uploading",
            filename=filename,
            s3_bucket=bucket,
            s3_key=key,
            bytes=size_bytes,
            checksum=checksum.lower(),
            edit_rate_num=num,
            edit_rate_den=den,
            upload_id=upload_id,
        )
    )


def restart_upload(s: Session, org_id: str, asset_id: str, upload_id: str) -> None:
    """A second `create` for the same content: same row, new multipart upload."""
    a = m.Asset.__table__
    s.execute(
        sa.update(a)
        .where(a.c.org_id == org_id, a.c.id == asset_id)
        .values(upload_id=upload_id, status="uploading", error=None)
    )


def mark_uploaded(s: Session, org_id: str, asset_id: str) -> None:
    """The object is complete. Probe has not run, so the asset is not ready.

    `upload_id` is cleared because it no longer refers to anything: an upload
    id survives its own completion only as a way to abort something that is
    already gone.
    """
    a = m.Asset.__table__
    s.execute(
        sa.update(a)
        .where(a.c.org_id == org_id, a.c.id == asset_id)
        .values(status="probing", upload_id=None, error=None)
    )


def mark_failed(s: Session, org_id: str, asset_id: str, error: dict) -> None:
    """Record why, in a shape a UI can render and a log filter cannot leak.

    `error` holds a code and counts. It must not hold a filename, a key or a
    presigned URL — the same rule as `mishne.logging`, and this column is read
    back into an API response, which is a wider audience than a log line.
    """
    a = m.Asset.__table__
    s.execute(
        sa.update(a)
        .where(a.c.org_id == org_id, a.c.id == asset_id)
        .values(status="failed", upload_id=None, error=error)
    )


def record_probe(
    s: Session,
    org_id: str,
    asset_id: str,
    *,
    rate_num: int,
    rate_den: int,
    drop_frame: bool,
    start_tc_frames: int,
    duration_frames: int,
    probe: dict,
    ingest_mode: str | None = None,
    status: str = "ready",
) -> None:
    """Stage 0's findings, and the end of the placeholder rate.

    Called by the probe-on-arrival path. Kept here rather than in the worker so
    that the one place an asset stops being provisional is the one place its
    invariants are written down.
    """
    a = m.Asset.__table__
    values: dict = {
        "edit_rate_num": rate_num,
        "edit_rate_den": rate_den,
        "drop_frame": drop_frame,
        "start_tc_frames": start_tc_frames,
        "duration_frames": duration_frames,
        "probe": probe,
        "probed_at": datetime.now(timezone.utc),
        "status": status,
        "error": None,
    }
    if ingest_mode is not None:
        values["ingest_mode"] = ingest_mode
    s.execute(sa.update(a).where(a.c.org_id == org_id, a.c.id == asset_id).values(**values))


def delete_asset(s: Session, org_id: str, asset_id: str) -> None:
    """Remove a row whose upload was abandoned before it ever completed.

    Only ever called for an asset still in `uploading`: once an object exists
    the row is the only record of what it is and who it belongs to, and deleting
    it is how an object becomes unattributable and permanent.
    """
    a = m.Asset.__table__
    s.execute(
        sa.delete(a).where(a.c.org_id == org_id, a.c.id == asset_id, a.c.status == "uploading")
    )
