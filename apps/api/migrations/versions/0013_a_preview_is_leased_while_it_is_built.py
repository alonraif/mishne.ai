"""A preview is leased while it is built.

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-02

0012 gave an asset a `proxy_status`, and `pending -> running` was guarded by a
conditional UPDATE so two workers could not encode the same asset twice. That is
enough while the only thing draining the queue is a single loop on the same
machine, and it stops being enough the moment the transcode moves to its own
fleet — which is the point of doing it at all, because ffmpeg at 100% of a
production API box is not a thing that may happen.

Two failures appear as soon as the worker is somewhere else, and neither is
visible today:

**A worker that dies mid-encode strands the row.** `running` is a state nothing
ever leaves on its own. The container is reaped by the scheduler, the task is
retried somewhere else, and the row sits `running` for ever — a preview that
never arrives and never says why. `proxy_claimed_at` makes the claim a *lease*:
a row whose lease has expired is evidence of a dead worker, and the queue can
hand it out again.

**A file ffmpeg cannot read is a poison pill.** Reclaiming a stale lease means a
row can be tried more than once, and something has to stop "more than once" from
becoming "for ever" on media that will never encode. `proxy_attempts` counts, and
the queue gives up at a threshold rather than burning a worker on it every few
minutes until somebody notices the bill.

## Backward compatibility

Two columns, one nullable and one defaulted, no constraint tightened. Release N
does not know they exist and its inserts keep working: `claim_proxy` in the old
release sets neither, and a row with a NULL `proxy_claimed_at` is simply one
whose lease cannot be judged — which the reclaimer treats as "leave alone"
rather than "expired", so an old and a new release can drain the same queue
without the old one's work being stolen.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from conventions import concurrent_index, drop_concurrent_index

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

#: The reclaimer's query. Partial, like the pending one in 0012: rows being
#: built are a handful at a time and every other row in the table is noise.
RUNNING_INDEX = "ix_assets_proxy_running"


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("proxy_claimed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "assets",
        sa.Column(
            "proxy_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.execute(
        "COMMENT ON COLUMN assets.proxy_claimed_at IS "
        "'When a worker took the lease on this preview. NULL when nothing holds "
        "it. A lease older than the configured timeout is evidence the worker "
        "died, and the row goes back in the queue.'"
    )
    op.execute(
        "COMMENT ON COLUMN assets.proxy_attempts IS "
        "'How many times a worker has started on this preview. Bounds the retry "
        "of media that will never encode.'"
    )
    concurrent_index(
        RUNNING_INDEX,
        "assets",
        ["proxy_claimed_at"],
        postgresql_where=sa.text("proxy_status = 'running'"),
    )


def downgrade() -> None:
    drop_concurrent_index(RUNNING_INDEX, "assets")
    op.drop_column("assets", "proxy_attempts")
    op.drop_column("assets", "proxy_claimed_at")
