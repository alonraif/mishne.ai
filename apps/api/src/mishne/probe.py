"""Stage 0, when an object lands — before any job exists.

An asset's rate, start timecode, duration and audio track count are needed
before a job can be priced: the credit estimate is a function of duration, and a
user is asked to approve it before anything runs. Waiting until job submission
to find out how long the file is would mean quoting a price after accepting the
work.

**This does not run in the API process.** Probing means reading the object, and
media never transits the API — that is the whole point of presigned uploads. In
production this is an S3 event notification on the `raw` bucket calling
`handle_s3_event`; locally it is `python -m mishne.probe <asset id>`. Either way
it is a worker, with a disk, that can afford to download a 30 GB AAF.

What it decides, in order:

* **The time base.** Rational, from the file. Until it runs, the asset row
  carries a placeholder rate and `probed_at IS NULL`.
* **Embedded or linked**, for an AAF. A linked sequence goes to
  `awaiting_media` with a row per file it is waiting for, and it is not an
  error state — nothing is wrong, and a job started against it would transcribe
  silence.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import Settings, get_settings, load_env_file
from .db import requirements as reqs
from .db import uploads
from .db.base import session_for_org
from .logging import get_logger
from .storage import ObjectRef, parse_source_key
from .workspace import S3Workspace, SourceFile

log = get_logger(__name__)


class ProbeError(RuntimeError):
    """A probe that failed for a reason the customer can act on."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


def probe_asset(org_id: str, asset_id: str, settings: Settings | None = None) -> str:
    """Probe one uploaded asset and record what stage 0 found. Returns its status."""
    settings = settings or get_settings()
    with session_for_org(org_id) as session:
        row = uploads.get_row(session, org_id, asset_id)
        if row is None:
            log.warning("probe.unknown_asset", asset_id=asset_id)
            return "unknown"
        if row.status == "uploading":
            # The object is not there yet. An event for a part, or a race with
            # the completion call; either way there is nothing to read.
            log.info("probe.not_uploaded", asset_id=asset_id)
            return row.status

        scratch = Path(settings.work_root) / "probe" / asset_id
        workspace = S3Workspace(
            org_id=org_id, project_id=row.project_id, scratch=scratch, settings=settings
        )
        try:
            path = workspace.materialise(
                asset_id,
                SourceFile(name=row.filename, ref=ObjectRef(row.s3_bucket, row.s3_key)),
            )
            if row.kind == "aaf":
                status = _probe_aaf(session, org_id, row, path)
            else:
                status = _probe_media(session, org_id, row, path)
        except ProbeError as exc:
            log.warning("probe.failed", asset_id=asset_id, code=exc.code)
            uploads.mark_failed(session, org_id, asset_id,
                                {"code": exc.code, "detail": exc.detail})
            return "failed"
        except Exception as exc:  # noqa: BLE001 - the customer gets a code, we get the type
            log.warning("probe.failed", asset_id=asset_id, reason=type(exc).__name__)
            uploads.mark_failed(session, org_id, asset_id, {"code": "probe_failed"})
            return "failed"
        finally:
            # The scratch copy is the point of staging, and it is also a 30 GB
            # file on a disk somebody pays for. It has served its purpose here.
            shutil.rmtree(scratch, ignore_errors=True)

        log.info("probe.done", asset_id=asset_id, status=status)
        return status


def _probe_media(session, org_id: str, row, path: Path) -> str:
    """A video file, or audio that arrived with a declared rate (ADR-0005)."""
    from .pipeline.steps import prepare
    from .timecode import Rate

    assume = None
    if row.kind == "audio":
        # Audio carries no frame rate, and guessing one is how a cut ends up a
        # frame out everywhere. The rate is declared at upload for exactly this
        # moment, and `create_asset` refuses an audio upload without one.
        assume = Rate(row.edit_rate_num, row.edit_rate_den)

    try:
        info = prepare.probe(path, assume_rate=assume)
    except ValueError as exc:
        raise ProbeError("no_frame_rate", str(exc)) from exc
    except FileNotFoundError as exc:  # ffprobe itself is missing
        raise ProbeError("probe_unavailable") from exc
    except RuntimeError as exc:
        raise ProbeError("unreadable_media") from exc

    uploads.record_probe(
        session,
        org_id,
        row.id,
        rate_num=info.rate.num,
        rate_den=info.rate.den,
        drop_frame=info.rate.drop_frame,
        start_tc_frames=info.start_tc_frames,
        duration_frames=info.duration_frames,
        probe={
            "codec": info.codec,
            "audio_tracks": len(info.audio),
            "has_video": info.has_video,
            "width": info.width,
            "height": info.height,
            "start_tc": info.start_tc,
            "channels": [track.channels for track in info.audio],
            "sample_rates": [track.sample_rate for track in info.audio],
        },
        status="ready",
    )
    return "ready"


def _probe_aaf(session, org_id: str, row, path: Path) -> str:
    """A sequence. Also decides whether it can be ingested on its own.

    ffprobe cannot read an AAF at all — it is structured storage, not a media
    container — so this branch exists because there is no other way to learn
    anything about the file.
    """
    from .pipeline.steps import aaf_ingest

    try:
        source = aaf_ingest.parse(path)
    except Exception as exc:  # noqa: BLE001 - pyaaf2 raises a wide variety
        raise ProbeError("unreadable_aaf", type(exc).__name__) from exc

    wanted = reqs.from_clips(source.clips)
    reqs.record(session, org_id, row.id, wanted)
    outstanding = reqs.outstanding(session, org_id, row.id)
    linked = bool(wanted)

    uploads.record_probe(
        session,
        org_id,
        row.id,
        rate_num=source.rate.num,
        rate_den=source.rate.den,
        drop_frame=source.rate.drop_frame,
        start_tc_frames=source.start_tc_frames,
        duration_frames=source.duration_frames,
        probe={
            "codec": "aaf",
            # The sequence's sound tracks — one per microphone on a recorded
            # conversation. This was 0, on the grounds that a sequence has no
            # track count in the sense ffprobe means; it does have one, `parse`
            # now reads every one of them (ADR-0019), and "0 audio tracks" on a
            # four-microphone podcast reads as a sequence with no sound in it.
            "audio_tracks": len(source.tracks),
            "clips": len(source.clips),
            "embedded": source.embedded,
            "unresolved_clips": len(source.missing),
            "notes": source.notes,
        },
        ingest_mode="aaf_linked" if linked else "aaf_embedded",
        status="awaiting_media" if outstanding else "ready",
    )
    return "awaiting_media" if outstanding else "ready"


def handle_s3_event(event: dict, settings: Settings | None = None) -> list[str]:
    """The entry point for an S3 `ObjectCreated` notification.

    Keys are parsed rather than looked up: the key scheme carries the org, the
    project and the asset, which is why it is worth being strict about. A key
    that is not a source object — a derived file, an artifact, anything at all
    under another prefix — is ignored rather than guessed at.
    """
    probed: list[str] = []
    for record in event.get("Records", []):
        key = record.get("s3", {}).get("object", {}).get("key", "")
        # S3 percent-encodes the key in notifications, and our keys have no
        # characters that survive the trip differently — but a key that needed
        # decoding and did not get it would parse as a different asset.
        from urllib.parse import unquote_plus

        parsed = parse_source_key(unquote_plus(key))
        if parsed is None:
            continue
        org_id, _project_id, asset_id = parsed
        probe_asset(org_id, asset_id, settings)
        probed.append(asset_id)
    return probed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m mishne.probe",
        description="Run stage 0 against an uploaded asset. In production this "
                    "is an S3 event; locally it is this.",
    )
    parser.add_argument("asset_id")
    parser.add_argument("--org", required=True, help="the asset's organisation")
    args = parser.parse_args(argv)
    # Like every other entry point: pydantic-settings reads `.env` but does not
    # export it, and the S3 adapter reads the environment. Without this, a probe
    # run by hand against MinIO addresses real AWS instead, fails a ClientError,
    # and marks a perfectly good asset `failed`.
    load_env_file(Path.cwd() / ".env")
    status = probe_asset(args.org, args.asset_id)
    print(f"{args.asset_id}: {status}")
    return 0 if status in ("ready", "awaiting_media") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
