"""An asset carries a preview rendition.

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-02

`docs/architecture/00-overview.md` listed proxy generation as out of scope, on
the grounds that the deliverable is a downloadable timeline rather than a
preview. That holds right up until someone has to *choose* the lines, which is
what the cut editor asks them to do. Reading a transcript tells you what was
said and nothing about whether the take was any good. ADR-0020 reverses it.

## Why these columns are on `assets`

A preview belongs to the upload, not to the job, for the same reason the
transcript does (ADR-0008): it is derived from the asset's own bytes, it is
identical for every job that draws on that asset, and a second job next month
should find it already made. Putting it on `jobs` would rebuild it per job and
would have nowhere to live for an asset nobody has cut yet.

It is deliberately *not* an `artifacts` row. `uq_artifacts_job_kind` makes an
artifact one-per-job-per-kind, and an artifact is a deliverable the customer
downloads. A preview is neither.

## Why the default is 'none' and not 'pending'

`pending` is the queue the proxy runner polls. A server default of `pending`
backfills every asset that already exists, and the runner then works its way
through the customer's entire back catalogue re-encoding footage nobody asked
about. `none` means "nobody has asked for a preview of this row", which is the
true statement about every asset written before this migration.

## Backward compatibility

Five columns, every one with a default or nullable, no constraint tightened on
existing data. Release N does not know they exist and its inserts keep working;
the CHECKs admit `'none'` and `''`, which is what those inserts produce. The
partial index is CONCURRENT because it is on `assets`, which is not a table
worth locking. The downgrade drops all of it and loses only the record of where
the previews are — the objects themselves expire on the derived bucket's own
clock.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from conventions import concurrent_index, drop_concurrent_index

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

# Spelled out locally, not imported from mishne.db.vocab, for the reason given
# in 0002: a migration is a historical record of the schema on this date and has
# to keep describing it after somebody appends a seventh proxy status to the
# live module.
PROXY_STATUSES = ("none", "pending", "running", "ready", "failed", "unsupported")
PROXY_KINDS = ("", "video", "audio")

#: Only the rows the runner is actually looking for. A full index on a status
#: column that is 'none' for almost every row is mostly dead weight.
PENDING_INDEX = "ix_assets_proxy_pending"


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column(
            "proxy_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'none'"),
        ),
    )
    op.add_column("assets", sa.Column("proxy_s3_key", sa.Text(), nullable=True))
    op.add_column(
        "assets",
        sa.Column("proxy_kind", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.add_column(
        "assets",
        sa.Column(
            "proxy_bytes", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("assets", sa.Column("proxy_error", JSONB(), nullable=True))

    op.create_check_constraint(
        "ck_assets_proxy_status", "assets", _in("proxy_status", PROXY_STATUSES)
    )
    op.create_check_constraint(
        "ck_assets_proxy_kind", "assets", _in("proxy_kind", PROXY_KINDS)
    )

    op.execute(
        "COMMENT ON COLUMN assets.proxy_status IS "
        "'How far the preview rendition has got. A separate axis from "
        "assets.status: an asset is ingestable long before it is playable, and "
        "a transcode that fails does not make the asset failed. none = nobody "
        "has asked; unsupported = there is nothing decodable behind this row.'"
    )
    op.execute(
        "COMMENT ON COLUMN assets.proxy_s3_key IS "
        "'Key in the derived bucket. NULL until the preview exists.'"
    )

    concurrent_index(
        PENDING_INDEX,
        "assets",
        ["created_at"],
        postgresql_where=sa.text("proxy_status = 'pending'"),
    )


def downgrade() -> None:
    drop_concurrent_index(PENDING_INDEX, "assets")
    op.drop_constraint("ck_assets_proxy_kind", "assets", type_="check")
    op.drop_constraint("ck_assets_proxy_status", "assets", type_="check")
    for column in (
        "proxy_error",
        "proxy_bytes",
        "proxy_kind",
        "proxy_s3_key",
        "proxy_status",
    ):
        op.drop_column("assets", column)
