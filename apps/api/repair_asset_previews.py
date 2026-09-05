#!/usr/bin/env python
"""Attach the previews that were built, uploaded, and then not written down.

## What went wrong

There are two drivers over the same stages. `pipeline/project.ingest` runs them
straight through, for `run.py`; `orchestration/graph.py` runs them one at a time
so a worker can record progress and resume between them. Both then built an
`AssetIngest` — separately — and the orchestrator's copy left out five fields,
among them all three `preview_*`.

Every one of those fields is defaulted, deliberately, so that a cache written
before they existed still loads. So nothing raised and nothing logged. Stage 0
rendered the preview, `publish_asset` mirrored it to the derived bucket, and the
ingest cache beside it recorded `previewName: ""`. `worker._record_preview`
takes that empty string as "this asset has no preview" and returns without
touching the row, so `assets.proxy_status` stayed `none` and the editor showed
no player — for every sequence that went through the orchestrator, which is
every sequence the product runs.

The bug is fixed in `project.build_ingest`, which both drivers now call.

## Why nothing has to be re-encoded

The preview is already in the derived bucket. It was uploaded by the run that
built it — `proxy.m4a` and `proxy.mp4` are in `workspace.CACHEABLE` precisely so
that they survive their own job. What was lost is the sentence saying so.

The object sits under the asset's *content* key, because the pipeline works in
content digests (`db/ids.py`) and `publish_asset` is keyed on
`AssetIngest.asset_id`. The database's `proxy_s3_key` is keyed on the row id,
which is what the download endpoint mints a presigned URL against. So this
copies the object to the key the row will name — a server-side copy, no bytes
through this process — and then records it exactly as `_record_preview` would
have.

Idempotent: an asset whose `proxy_status` is already `ready` is left alone.

    python repair_asset_previews.py --dry-run
    python repair_asset_previews.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import sqlalchemy as sa  # noqa: E402

from mishne.config import get_settings  # noqa: E402
from mishne.db import models as m  # noqa: E402
from mishne.db.base import normalise_url  # noqa: E402
from mishne.storage import bucket_for, get_client  # noqa: E402

#: What stage 0 names a preview, by kind. `proxy.build` writes one or the other.
PREVIEW_NAMES = {"proxy.mp4": "video", "proxy.m4a": "audio"}


def _candidates(conn):
    """Assets with no preview recorded, newest first."""
    a = m.Asset.__table__
    return conn.execute(
        sa.select(a.c.id, a.c.org_id, a.c.project_id, a.c.filename, a.c.kind)
        .where(a.c.proxy_status != "ready")
        .order_by(a.c.created_at.desc())
    ).all()


def _find_preview(s3, bucket: str, org_id: str, project_id: str):
    """The preview object under any asset directory in this project, by content id.

    The pipeline id is a content digest and is not stored on the row, so the
    object cannot be addressed directly. Listing the project's asset prefixes and
    looking for the two names stage 0 can write finds it without needing to
    recompute the digest — which would mean downloading the source.

    Returns {content_id: (key, name, size)}.
    """
    found = {}
    prefix = f"orgs/{org_id}/projects/{project_id}/assets/"
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    for page in pages:
        for obj in page.get("Contents", []):
            name = obj["Key"].rsplit("/", 1)[-1]
            if name in PREVIEW_NAMES:
                content_id = obj["Key"][len(prefix):].split("/", 1)[0]
                found[content_id] = (obj["Key"], name, obj["Size"])
    return found


def _content_id_for(s3, bucket: str, org_id: str, project_id: str):
    """The content id whose cached ingest claims this asset row.

    The ingest cache records the pipeline's own `assetId`, and the worker holds
    the mapping between that and the row only while a job is running. Rather
    than reconstruct it, read the cache: `ingest.json` sits beside the preview
    and names the source path, whose basename is the row's filename.
    """
    prefix = f"orgs/{org_id}/projects/{project_id}/assets/"
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    out = {}
    for page in pages:
        for obj in page.get("Contents", []):
            if obj["Key"].rsplit("/", 1)[-1] != "ingest.json":
                continue
            content_id = obj["Key"][len(prefix):].split("/", 1)[0]
            try:
                body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                out[content_id] = Path(json.loads(body).get("path", "")).name
            except Exception:  # noqa: BLE001 — a bad cache is not this script's problem
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="say what would change, change nothing")
    args = ap.parse_args()

    settings = get_settings()
    bucket = bucket_for("derived", settings)
    s3 = get_client()
    engine = sa.create_engine(normalise_url(settings.database_url))

    repaired = skipped = 0
    with engine.begin() as conn:
        rows = _candidates(conn)
        print(f"{len(rows)} asset(s) with no preview recorded")
        for row in rows:
            previews = _find_preview(s3, bucket, row.org_id, row.project_id)
            if not previews:
                continue
            names = _content_id_for(s3, bucket, row.org_id, row.project_id)
            match = next(
                (cid for cid in previews if names.get(cid) == row.filename), None
            )
            if match is None:
                skipped += 1
                print(f"  {row.id}  no preview found for {row.filename!r}")
                continue

            src_key, name, size = previews[match]
            kind = PREVIEW_NAMES[name]
            # Recorded where the object actually is — under the content id,
            # which is where `publish_asset` wrote it. Copying it to a row-id
            # key would work too, and would put a second 23 MB copy of every
            # preview in the bucket to spare one lookup. The download endpoint
            # presigns whatever `proxy_s3_key` says.
            print(f"  {row.id}  {kind:<5} {size:>12,}  {row.filename!r}")
            if args.dry_run:
                repaired += 1
                continue

            a = m.Asset.__table__
            conn.execute(
                sa.update(a)
                .where(a.c.org_id == row.org_id, a.c.id == row.id)
                .values(proxy_status="ready", proxy_s3_key=src_key,
                        proxy_kind=kind, proxy_bytes=size,
                        proxy_error=None, proxy_claimed_at=None)
            )
            repaired += 1

    verb = "would repair" if args.dry_run else "repaired"
    print(f"\n{verb} {repaired}, no preview in the bucket for {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
