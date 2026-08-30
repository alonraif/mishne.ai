"""The ledger outlives what it refers to.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-29

Found by B3, by deleting a project in a test teardown:

    ERROR: credit_ledger is append-only: UPDATE is not permitted
    CONTEXT: SQL statement "UPDATE ONLY credit_ledger SET project_id = NULL ..."

`credit_ledger.project_id` and `job_id` were foreign keys with `ON DELETE SET
NULL`. Deleting a project therefore makes Postgres *update* the ledger, and the
append-only trigger (0001, ADR-0006) refuses — correctly. The two rules were
each right and together they made a project with any billing history
undeletable, which C4's retention and deletion work would have run into with a
customer's data rather than a test's.

The fix is to stop pointing at the other tables. `org_id` is already a plain
column on every table for exactly this reason — uniform policies, no path where
a forgotten join leaks, and nothing that has to cascade. The ledger's job is to
say what happened and what it cost; a project that no longer exists does not
change either, and a financial record that mutates when unrelated rows are
deleted is not a financial record.

Expand-only: dropping a constraint cannot break a release that does not know it
was dropped, and no column, value or index changes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Named by 0001's convention; dropped by name so this is explicit about
    # which two constraints are going.
    op.drop_constraint("credit_ledger_project_id_fkey", "credit_ledger", type_="foreignkey")
    op.drop_constraint("credit_ledger_job_id_fkey", "credit_ledger", type_="foreignkey")

    # The ids stay, and stay indexed: "what did this project cost" is still a
    # question, and it is now one the ledger answers on its own.
    op.execute(
        "COMMENT ON COLUMN credit_ledger.project_id IS "
        "'The project this entry was for. Not a foreign key: the ledger is "
        "append-only and must survive the project being deleted.'"
    )
    op.execute(
        "COMMENT ON COLUMN credit_ledger.job_id IS "
        "'The job this entry was for. Not a foreign key, for the same reason.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON COLUMN credit_ledger.project_id IS NULL")
    op.execute("COMMENT ON COLUMN credit_ledger.job_id IS NULL")
    op.create_foreign_key(
        "credit_ledger_project_id_fkey", "credit_ledger", "projects",
        ["project_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "credit_ledger_job_id_fkey", "credit_ledger", "jobs",
        ["job_id"], ["id"], ondelete="SET NULL",
    )
