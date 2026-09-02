"""Build one asset's preview. One asset, one process, any environment.

The counterpart of `worker.py`, and the same division of labour: that module
runs one job and knows nothing about what decided to run it, and this runs one
preview and knows nothing about whether a poll or a queue message asked for it.

**This is the module that is meant to run somewhere else.** ffmpeg over a
three-hour master saturates whatever it is given for as long as it takes, which
is correct for a transcoder and an outage for anything sharing the machine. So
in production this is the entry point of a preview fleet — its own task
definition, its own instance class, scaled on queue depth — and nothing about
the code changes to put it there. See ADR-0021.

    python -m mishne.orchestration.proxyworker --org org_x ast_y

Everything it touches is reachable from those two ids: the source in the raw
bucket, the row to claim, the derived bucket to publish to. Nothing is passed
in, which is what lets a queue message be forty bytes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa

from ..config import Settings, get_settings, load_env_file
from ..db import uploads
from ..db.base import session_for_org
from ..logging import configure as configure_logging
from ..logging import get_logger
from ..pipeline.steps import prepare
from ..pipeline.steps import proxy as proxy_step
from ..storage import ObjectRef, Storage, bucket_for, derived_key
from ..timecode import Rate

log = get_logger(__name__)


def build_proxy(org_id: str, asset_id: str, settings: Settings | None = None) -> str:
    """One asset's preview, start to finish. Returns its resulting status.

    **The unit of work, and the thing a machine other than the API box runs.**
    Separated from whatever found the work, because what finds it differs by
    environment — a poll on a laptop, a queue message in production — while this
    does not.

    Safe to call on a row that is not `pending`: `claim_proxy` is the guard, and
    losing the race to another worker is an ordinary outcome, not an error.
    """
    settings = settings or get_settings()
    storage = Storage(settings)

    with session_for_org(org_id) as s:
        row = s.execute(
            sa.text(
                "SELECT id, project_id, kind, filename, s3_bucket, s3_key, "
                "       edit_rate_num, edit_rate_den, proxy_status "
                "FROM assets WHERE id = :id"
            ),
            {"id": asset_id},
        ).first()
        if row is None:
            return "none"
        if not row.s3_key:
            uploads.fail_proxy(s, org_id, asset_id, reason="no_object",
                               permanent=True)
            return "unsupported"
        if not uploads.claim_proxy(s, org_id, asset_id):
            # Another runner has it, or it is already built. Either way there is
            # nothing here to do and nothing has gone wrong.
            return row.proxy_status
        project_id = row.project_id
        kind = row.kind
        filename = row.filename
        bucket = row.s3_bucket or bucket_for("raw", settings)
        key = row.s3_key
        # Audio carries no frame rate of its own; the one declared at upload is
        # the only correct answer, and guessing is how a preview ends up a frame
        # out from the transcript everywhere.
        assume = Rate(row.edit_rate_num, row.edit_rate_den) if kind == "audio" else None

    work_root = Path(settings.work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=f"proxy_{asset_id}_", dir=work_root))
    try:
        # `filename` only to give the file a sane extension for ffmpeg's
        # demuxer probing. The key is the identity; this is a hint.
        local = scratch / f"source{Path(filename).suffix.lower()}"
        storage.download(ObjectRef(bucket=bucket, key=key), local)

        try:
            info = prepare.probe(local, assume_rate=assume)
        except (ValueError, RuntimeError) as exc:
            # Nothing decodable behind the row, or no frame rate to be had.
            # Asking again will produce the same answer, so this is an answer.
            _fail(org_id, asset_id, type(exc).__name__, permanent=True)
            return "unsupported"

        try:
            built = proxy_step.build(
                info, scratch, threads=settings.proxy_ffmpeg_threads
            )
        except proxy_step.ProxyError as exc:
            # Deterministic: the same bytes through the same command produce the
            # same failure, so retrying is not a plan. `verify` refusing a
            # duration mismatch lands here too, which is the point of it.
            log.warning("proxy.refused", asset_id=asset_id,
                        reason=type(exc).__name__)
            _fail(org_id, asset_id, type(exc).__name__, permanent=True)
            return "unsupported"

        s3_key = derived_key(org_id, project_id, asset_id, built.name)
        storage.upload(built.path, ObjectRef(
            bucket=bucket_for("derived", settings), key=s3_key
        ))
        with session_for_org(org_id) as s:
            uploads.record_proxy(s, org_id, asset_id, s3_key=s3_key,
                                 kind=built.kind, size_bytes=built.bytes)
        log.info("proxy.ready", asset_id=asset_id, kind=built.kind,
                 bytes=built.bytes)
        return "ready"

    except Exception as exc:  # noqa: BLE001 — infrastructure, not the media
        # A download that timed out or a disk that filled. Distinct from the
        # cases above: this one may well work next time, so it is `failed`
        # rather than `unsupported` and it does not claim to be a verdict on
        # the customer's footage.
        log.error("proxy.failed", asset_id=asset_id, reason=type(exc).__name__)
        _fail(org_id, asset_id, type(exc).__name__, permanent=False)
        return "failed"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _fail(org_id: str, asset_id: str, reason: str, *, permanent: bool) -> None:
    """Record why, in its own session, so it survives the one that raised."""
    try:
        with session_for_org(org_id) as s:
            uploads.fail_proxy(s, org_id, asset_id, reason=reason,
                               permanent=permanent)
    except Exception:  # noqa: BLE001 — nothing left to do about it
        log.error("proxy.could_not_mark_failed", asset_id=asset_id)


def main(argv: list[str] | None = None) -> int:
    # A worker started by hand reads the same .env the API does. In a deployed
    # container there is no file and the environment is the environment.
    load_env_file(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(
        prog="python -m mishne.orchestration.proxyworker",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("asset_id")
    parser.add_argument("--org", required=True)
    args = parser.parse_args(argv)

    configure_logging()
    status = build_proxy(args.org, args.asset_id)
    print(f"{args.asset_id}: preview {status}")
    return 0 if status in ("ready", "unsupported") else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
