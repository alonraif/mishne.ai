"""A beat carved into two spans is two clips, not a constraint violation.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-31

`selections` carried `UNIQUE (job_id, beat_id)` — one appearance per beat in a
cut — while its own class docstring says the opposite three lines above it:

    A selection is not always a whole beat: stage 6 proposes narrower spans
    carved from one (ADR-0010), which is why the in/out timecodes are here
    rather than read off the beat.

Both cannot be true. ADR-0010 is the product: a long answer is carved into
candidate spans, the solver may take two of them and leave the middle out, and
that is two clips on the timeline from one beat. The constraint forbade exactly
the case the columns beside it exist to express.

It never fired because nothing wrote to the table. The first worker run that
persisted a real cut would have hit it — after the artifacts were published and
the customer was charged, at the last write of the job.

`UNIQUE (job_id, order_idx)` stays and is the one that matters: it is what makes
a cut an ordered list with no duplicate positions. Nothing else depended on the
dropped constraint; `repository.get_transcript` joined `selections` to `beats`
one-to-one on the strength of it, and now aggregates instead — a beat cut twice
reports its first position, because `Beat.orderIdx` in the API is beat-level and
the timeline is the record of the rest.

## Contract, not expand

Dropping a unique constraint cannot break a reader: every row that was legal
before is still legal, and release N inserts fewer shapes than release N+1, not
more. The ordering rule in the migrations README is about the reverse case.
"""

from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_selections_job_beat", "selections", type_="unique")
    op.execute(
        "COMMENT ON TABLE selections IS "
        "'The cut: one row per span, in cut order. A beat may appear more than "
        "once — stage 6 carves candidate spans out of one beat and the solver "
        "may select two of them (ADR-0010). Uniqueness is on (job_id, "
        "order_idx), which is what makes this an ordered list.'"
    )


def downgrade() -> None:
    op.execute("COMMENT ON TABLE selections IS NULL")
    # Recreating it will fail on any job whose cut used two spans from one
    # beat, which is the point of removing it.
    op.create_unique_constraint(
        "uq_selections_job_beat", "selections", ["job_id", "beat_id"]
    )
