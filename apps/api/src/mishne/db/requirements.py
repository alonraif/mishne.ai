"""What a linked AAF is still waiting for.

An AAF is a sequence, not a container. An *embedded* one carries its essence
inside itself; a *linked* one is a few hundred kilobytes of pointers at media on
an editor's SAN, and uploading it alone gets you a transcript of silence.
`aaf_ingest.parse` already reports which clips it could not resolve — this
module makes that finding durable, so the UI can ask for exactly the files that
are missing and a job can refuse to start until they arrive.

**Matching is on basename**, normalised. The absolute path inside an AAF
describes a filesystem we will never see, and `aaf_ingest._url_to_path` already
falls back to a same-directory basename match for exactly this reason —
materialising the companions beside the AAF is therefore all the resolution that
is needed, and the parser does the rest unchanged.

A basename is not unique in principle: two source files really can both be
called `A001.mxf`, and the AAF gives us nothing better to key on. `mob_id` is
recorded so a stricter check is possible later without a second migration.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import sqlalchemy as sa
from sqlalchemy.orm import Session

from . import models as m


@dataclass(frozen=True)
class Requirement:
    """One file a sequence references and does not contain."""

    basename: str
    match_key: str
    clip_count: int
    mob_id: str | None = None
    clip_name: str | None = None


def basename_of(url: str | None) -> str:
    """The filename an AAF locator points at, whatever shape the locator is in.

    Locators in the wild are `file:///Volumes/...`, bare Windows paths with
    backslashes, percent-encoded URLs, and occasionally nothing at all. All that
    is wanted is the last segment.
    """
    if not url:
        return ""
    raw = unquote(urlparse(url).path or url) if "://" in url else unquote(url)
    return raw.replace("\\", "/").rstrip("/").split("/")[-1]


def match_key_for(basename: str) -> str:
    """Case- and separator-insensitive, and stored rather than computed.

    Stored so the unique constraint, the index and the lookup are the same
    expression: a functional index and a hand-written `lower()` drift apart the
    first time somebody edits one of them. Windows and macOS are both
    case-insensitive, so `A001.MXF` and `a001.mxf` are one file to the editor
    who will upload it, and telling them otherwise would be a bug they cannot
    act on.
    """
    return basename.strip().lower()


def from_clips(clips) -> list[Requirement]:
    """Fold unresolved clips into one requirement per referenced file.

    Per file, not per clip: a forty-clip sequence cut from three rushes is three
    things to ask for. `clip_count` is kept because it orders the list — the
    file that unblocks forty clips is the one worth asking for first.
    """
    found: dict[str, dict] = {}
    for clip in clips:
        if getattr(clip, "media_path", None) is not None:
            continue
        if getattr(clip, "embedded_mob_id", None):
            continue
        basename = basename_of(getattr(clip, "target_url", None))
        if not basename:
            # A clip with no locator at all. The clip *name* is not a filename —
            # it is whatever the editor typed — and asking for it would send the
            # customer looking for a file that does not exist. The parse already
            # reports the clip as unresolved; that is the honest end of it.
            continue
        key = match_key_for(basename)
        entry = found.setdefault(
            key,
            {"basename": basename, "count": 0,
             "mob_id": getattr(clip, "mob_id", None) or None,
             "clip_name": clip.name or None},
        )
        entry["count"] += 1
    return [
        Requirement(
            basename=e["basename"], match_key=key, clip_count=e["count"],
            mob_id=e["mob_id"], clip_name=e["clip_name"],
        )
        for key, e in sorted(found.items(), key=lambda kv: -kv[1]["count"])
    ]


def _row_id(asset_id: str, match_key: str) -> str:
    """Deterministic, so re-probing an asset updates rather than duplicates."""
    digest = hashlib.sha256(f"{asset_id}:{match_key}".encode()).hexdigest()
    return f"req_{digest[:16]}"


def record(s: Session, org_id: str, asset_id: str, wanted: list[Requirement]) -> int:
    """Replace this asset's requirements with what the parse just found.

    Replace rather than merge: a re-probe reflects the file as it is now, and a
    requirement that has gone away — because the customer re-exported the
    sequence with embedded essence — must not linger and block a job forever.
    Anything already satisfied keeps its satisfaction, because the file it
    refers to is still uploaded.
    """
    table = m.AssetMediaRequirement.__table__
    keep = {r.match_key for r in wanted}
    existing = {
        row.match_key: row
        for row in s.execute(
            sa.select(table).where(table.c.org_id == org_id, table.c.asset_id == asset_id)
        ).all()
    }
    stale = [key for key in existing if key not in keep]
    if stale:
        s.execute(
            sa.delete(table).where(
                table.c.org_id == org_id,
                table.c.asset_id == asset_id,
                table.c.match_key.in_(stale),
            )
        )
    for want in wanted:
        if want.match_key in existing:
            s.execute(
                sa.update(table)
                .where(table.c.org_id == org_id, table.c.asset_id == asset_id,
                       table.c.match_key == want.match_key)
                .values(basename=want.basename, clip_count=want.clip_count,
                        mob_id=want.mob_id, clip_name=want.clip_name)
            )
        else:
            s.execute(
                sa.insert(table).values(
                    id=_row_id(asset_id, want.match_key),
                    org_id=org_id,
                    asset_id=asset_id,
                    basename=want.basename,
                    match_key=want.match_key,
                    mob_id=want.mob_id,
                    clip_name=want.clip_name,
                    clip_count=want.clip_count,
                )
            )
    return len(wanted)


def for_asset(s: Session, org_id: str, asset_id: str) -> list[sa.Row]:
    table = m.AssetMediaRequirement.__table__
    return list(
        s.execute(
            sa.select(table)
            .where(table.c.org_id == org_id, table.c.asset_id == asset_id)
            .order_by(table.c.satisfied_at.is_(None).desc(), table.c.clip_count.desc())
        ).all()
    )


def outstanding(s: Session, org_id: str, asset_id: str) -> int:
    table = m.AssetMediaRequirement.__table__
    return int(
        s.execute(
            sa.select(sa.func.count())
            .select_from(table)
            .where(
                table.c.org_id == org_id,
                table.c.asset_id == asset_id,
                table.c.satisfied_by_asset_id.is_(None),
            )
        ).scalar_one()
    )


def satisfy(s: Session, org_id: str, asset_id: str, filename: str) -> list[str]:
    """A file just landed. Which sequences in this org were waiting for it?

    Returns the asset ids that were waiting, so the caller can re-check whether
    any of them is now complete. This is the query `ix_asset_media_requirements_match`
    exists for: it leads with `org_id` because the policy filters on it, and an
    index that does not lead with it is not the access path.
    """
    table = m.AssetMediaRequirement.__table__
    key = match_key_for(basename_of(filename) or filename)
    if not key:
        return []
    waiting = s.execute(
        sa.select(table.c.asset_id).where(
            table.c.org_id == org_id,
            table.c.match_key == key,
            table.c.satisfied_by_asset_id.is_(None),
            # A sequence cannot satisfy its own requirement.
            table.c.asset_id != asset_id,
        )
    ).scalars().all()
    if not waiting:
        return []
    s.execute(
        sa.update(table)
        .where(
            table.c.org_id == org_id,
            table.c.match_key == key,
            table.c.satisfied_by_asset_id.is_(None),
            table.c.asset_id != asset_id,
        )
        .values(satisfied_by_asset_id=asset_id, satisfied_at=datetime.now(timezone.utc))
    )
    return sorted(set(waiting))


def refresh_status(s: Session, org_id: str, asset_id: str) -> str | None:
    """Move an asset between `awaiting_media` and `ready` as files arrive.

    Only ever moves an asset that is already in one of those two states. An
    asset that is uploading, probing or failed has a status that means something
    else, and a companion file arriving does not change it.
    """
    a = m.Asset.__table__
    row = s.execute(
        sa.select(a.c.status).where(a.c.org_id == org_id, a.c.id == asset_id)
    ).first()
    if row is None or row.status not in ("awaiting_media", "ready"):
        return None
    target = "awaiting_media" if outstanding(s, org_id, asset_id) else "ready"
    if target != row.status:
        s.execute(
            sa.update(a).where(a.c.org_id == org_id, a.c.id == asset_id).values(status=target)
        )
    return target


def satisfied_by(s: Session, org_id: str, asset_id: str) -> list[str]:
    """Companion assets this sequence needs materialised beside it at ingest."""
    table = m.AssetMediaRequirement.__table__
    return list(
        s.execute(
            sa.select(table.c.satisfied_by_asset_id).where(
                table.c.org_id == org_id,
                table.c.asset_id == asset_id,
                table.c.satisfied_by_asset_id.is_not(None),
            )
        ).scalars().all()
    )
