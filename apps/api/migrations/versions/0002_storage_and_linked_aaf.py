"""Storage columns, and the media a linked AAF is waiting for.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-29

Workstream B2. The first migration that has to obey the expand/contract rules
for real — 0001 created every table it touched, so nothing was writing to them.
This one runs against a database that release N is still talking to, so:

* Every column added to `assets` is **nullable**. Release N does not know they
  exist and its inserts must keep working.
* The two CHECK constraints on `assets` are **widened**, never tightened. A
  widened CHECK accepts everything the old release writes plus one more value,
  so dropping and re-adding it inside this migration is safe. Going the other
  way — removing a value — is a contract step, needs its own release, and needs
  proof that no row and no running code still uses it.
* `asset_media_requirements` is new, so `NOT NULL` is correct on its columns for
  the same reason it was correct throughout 0001.

## Why a table rather than a jsonb blob on `assets`

The obvious cheaper shape is `assets.missing_media jsonb`. It loses the thing
the feature is actually about: *given a file the customer just uploaded, which
sequences were waiting for it?* That is a lookup across every awaiting asset in
the org, on every completed upload. As rows it is one index; as a jsonb array it
is a scan of every asset with a containment operator, and it gets slower exactly
as a customer's project gets big enough to matter.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from conventions import concurrent_index, create_org_table, drop_concurrent_index

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)

# Spelled out locally, not imported from mishne.db.vocab. A migration is a
# historical record of the schema on this date and must keep describing it after
# somebody appends a fourth ingest mode to the live module. Same reasoning as
# the block at the top of 0001.
INGEST_MODES_OLD = ("full_media", "aaf_embedded", "audio_only")
INGEST_MODES_NEW = ("full_media", "aaf_embedded", "audio_only", "aaf_linked")
ASSET_STATUSES_OLD = ("uploading", "probing", "ready", "failed")
ASSET_STATUSES_NEW = ("uploading", "probing", "ready", "failed", "awaiting_media")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _widen(table: str, name: str, column: str, values: tuple[str, ...]) -> None:
    """Replace a closed-vocabulary CHECK with a strictly larger one.

    Not `NOT VALID` plus a later `VALIDATE`: that dance exists for constraints
    that might reject existing rows, and a superset cannot. Every row that
    passed the old constraint passes this one by construction, so the validating
    scan is quick and correct in one step.
    """
    op.drop_constraint(name, table, type_="check")
    op.create_check_constraint(name, table, _in(column, values))


def upgrade() -> None:
    # ── assets: what the upload path needs to record ───────────────────────
    #
    # Nullable, every one of them. `server_default` is deliberately not used
    # even where a default would read nicely: adding one to a large table is a
    # metadata-only operation on modern Postgres, but the value would then
    # appear on rows written by the old release as though it had meant it.
    op.add_column("assets", sa.Column("upload_id", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("error", JSONB(), nullable=True))
    op.add_column("assets", sa.Column("probed_at", TS, nullable=True))

    _widen("assets", "ck_assets_ingest_mode", "ingest_mode", INGEST_MODES_NEW)
    _widen("assets", "ck_assets_status", "status", ASSET_STATUSES_NEW)

    # An upload is content-addressed by `checksum`, which 0001 already created.
    # Two projects uploading the same rushes are two asset rows and one
    # transcription, and this index is what makes finding the earlier one a
    # lookup. Partial, because an asset that has not finished uploading has no
    # checksum yet and there is no point indexing the nulls.
    concurrent_index(
        "ix_assets_org_checksum",
        "assets",
        ["org_id", "checksum"],
        postgresql_where=sa.text("checksum IS NOT NULL"),
    )

    # ── the media a linked AAF is waiting for ──────────────────────────────
    create_org_table(
        "asset_media_requirements",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Text(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("basename", sa.Text(), nullable=False),
        sa.Column("match_key", sa.Text(), nullable=False),
        sa.Column("mob_id", sa.Text(), nullable=True),
        sa.Column("clip_name", sa.Text(), nullable=True),
        sa.Column("clip_count", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "satisfied_by_asset_id",
            sa.Text(),
            sa.ForeignKey("assets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("satisfied_at", TS, nullable=True),
        sa.UniqueConstraint("asset_id", "match_key", name="uq_asset_media_req"),
    )

    # An unindexed foreign key turns the ON DELETE CASCADE from `assets` into a
    # sequential scan of this table per deleted asset.
    concurrent_index(
        "ix_asset_media_requirements_asset", "asset_media_requirements", ["asset_id"]
    )
    # The resolution query: a file just landed — which sequences wanted it?
    concurrent_index(
        "ix_asset_media_requirements_match",
        "asset_media_requirements",
        ["org_id", "match_key"],
    )


def downgrade() -> None:
    """A real downgrade, per migrations/README.md.

    The narrowing of the two CHECK constraints is the part that can genuinely
    fail, and it should: rows written while this release was live may hold
    `aaf_linked` or `awaiting_media`, and a downgrade that silently discarded
    them would be worse than one that stops. The rows are moved to the nearest
    honest value in the old vocabulary first — a linked AAF becomes a failed
    asset, which is what release N would have called it, since to that code an
    AAF it cannot resolve is exactly a failure.
    """
    drop_concurrent_index("ix_asset_media_requirements_match", "asset_media_requirements")
    drop_concurrent_index("ix_asset_media_requirements_asset", "asset_media_requirements")
    op.drop_table("asset_media_requirements")

    op.execute(
        "UPDATE assets SET status = 'failed' WHERE status = 'awaiting_media'"
    )
    op.execute(
        "UPDATE assets SET ingest_mode = 'aaf_embedded' WHERE ingest_mode = 'aaf_linked'"
    )
    _widen("assets", "ck_assets_status", "status", ASSET_STATUSES_OLD)
    _widen("assets", "ck_assets_ingest_mode", "ingest_mode", INGEST_MODES_OLD)

    drop_concurrent_index("ix_assets_org_checksum", "assets")
    op.drop_column("assets", "probed_at")
    op.drop_column("assets", "error")
    op.drop_column("assets", "upload_id")
