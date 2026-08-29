"""The closed vocabularies, in one place.

`text` columns with a CHECK constraint, never a native Postgres ENUM: a value
cannot be removed from an enum type, so a migration that adds one has no honest
downgrade. A CHECK can be dropped and re-added freely, which is what makes the
expand/contract dance possible on a status column.

These mirror the Literal types in `mishne.schemas` and
`packages/shared/src/types.ts`. All three have to move together.
"""

from __future__ import annotations

ORG_TIERS = ("starter", "pro", "studio")
USER_ROLES = ("owner", "member", "viewer")

ASSET_KINDS = ("video", "aaf", "audio")
INGEST_MODES = ("full_media", "aaf_embedded", "audio_only")
ASSET_STATUSES = ("uploading", "probing", "ready", "failed")

JOB_MODES = ("ai", "manual", "hybrid")
JOB_STATUSES = (
    "estimating",
    "awaiting_approval",
    "awaiting_edit",
    "queued",
    "preparing",
    "transcribing",
    "analyzing",
    "selecting",
    "assembling",
    "validating",
    "complete",
    "failed",
    "cancelled",
)
JOB_TERMINAL_STATUSES = ("complete", "failed", "cancelled")
STEP_STATUSES = ("pending", "active", "done", "failed")

ARTIFACT_KINDS = ("aaf", "fcpxml", "edl", "otio", "json")
SPEAKER_SOURCES = ("track", "diarization")
LEDGER_KINDS = ("purchase", "grant", "hold", "release", "settle", "refund", "adjustment")

#: Which NLE each interchange format is for. A label, not data — derived on read
#: rather than stored, so a wording change is not a migration.
TARGET_NLE = {
    "aaf": "Avid Media Composer",
    "fcpxml": "Premiere Pro · Resolve · Final Cut",
    "edl": "Universal fallback",
    "otio": "Canonical timeline",
    "json": "Inspection",
}


def check(column: str, values: tuple[str, ...]) -> str:
    """A CHECK expression for a closed vocabulary."""
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"
