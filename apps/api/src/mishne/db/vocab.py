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
# How a user authenticated. `local` is email and password, which is what a
# developer's machine and the test suite use; `workos` is the hosted identity
# provider the security design names, and the one that carries SAML and SCIM.
AUTH_PROVIDERS = ("local", "workos")

ASSET_KINDS = ("video", "aaf", "audio")
# `aaf_linked` is an AAF whose clips point at external media the customer has to
# upload alongside it. `aaf_embedded` carries its essence inside the file and
# needs nothing else. The distinction is not cosmetic: a linked AAF is not
# ingestable on its own, and the difference decides whether an asset can go
# straight to `ready`.
INGEST_MODES = ("full_media", "aaf_embedded", "audio_only", "aaf_linked")
# `awaiting_media` is a linked AAF that probed cleanly and is waiting for the
# files it references. It is not `failed` — nothing is wrong — and it is not
# `ready`, because a job started against it would transcribe silence.
ASSET_STATUSES = ("uploading", "probing", "ready", "failed", "awaiting_media")
# Where the asset's preview rendition has got to. A separate axis from
# `ASSET_STATUSES` on purpose: an asset is ingestable long before it is
# playable, and a transcode that fails must not make the asset itself failed.
# `none` is an asset nobody has asked for a preview of — every row that existed
# before previews did, which is why it is the column default and `pending` is
# not. `unsupported` is a decided answer, not a pending one: there is nothing
# decodable behind this row and asking again will not change that.
PROXY_STATUSES = (
    "none",
    "pending",
    "running",
    "ready",
    "failed",
    "unsupported",
)
#: What the preview actually is. A sequence has no video to show, so an AAF's
#: preview is its flattened sound mix and nothing else (ADR-0019, ADR-0020).
PROXY_KINDS = ("", "video", "audio")

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
