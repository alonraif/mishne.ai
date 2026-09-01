"""A job is called something the person who submitted it chose.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-01

Until now a job's only name was its primary key — `job_8a98a1ca` — and that is
what every screen showed. It is a fine identifier and a terrible label: an
editor with four cuts of the same interview cannot tell which is the web
version and which is the one for broadcast, and the id says nothing about
either. Notes were the only distinguishing text on the list, and a
transcribe-only job has no notes at all.

So the name is asked for at submission and stored here. It is not unique and it
is not an identifier: two jobs may be called "web cut" and the id is still what
everything references. It exists to be read.

## Why it is NOT NULL with a default rather than nullable

A nullable name is a name every renderer has to have an opinion about, and the
opinions drift — one screen falls back to the id, another prints "Untitled",
a third prints nothing. `''` is not a state the API produces: `routers/jobs`
derives a name from the first asset's filename when the client sends none, so
the column is non-empty by the time anything reads it. The default is here for
the rows that existed before this migration, and those are backfilled below.

## The backfill

Existing jobs are named after the first upload they draw on, minus its
extension — which is the same rule submission now uses, so old jobs and new
ones read alike. A job whose assets have since been deleted falls back to its
creation date, because a list sorted by date whose rows all say "Untitled job"
is worse than one that repeats itself.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("name", sa.Text(), nullable=False, server_default=sa.text("''")),
    )
    op.execute(
        """
        UPDATE jobs j
           SET name = COALESCE(
                 (SELECT regexp_replace(a.filename, '\\.[^./\\\\]+$', '')
                    FROM job_assets ja
                    JOIN assets a ON a.id = ja.asset_id
                   WHERE ja.job_id = j.id
                   ORDER BY ja.order_idx
                   LIMIT 1),
                 'Job of ' || to_char(j.created_at, 'DD Mon YYYY')
               )
         WHERE j.name = ''
        """
    )
    op.execute(
        "COMMENT ON COLUMN jobs.name IS "
        "'What the customer calls this cut. Not unique and not an identifier — "
        "the id is. Never empty: submission derives one from the first asset "
        "when the client sends none.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN jobs.name IS NULL")
    op.drop_column("jobs", "name")
