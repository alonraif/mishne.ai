"""What a job cost, and which stage and which asset spent it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-30

Workstream C3. `jobs.cost_cents` has existed since 0001 and nothing has ever
written to it: the router's ledger knows what a job spent on models and the
worker discards it. `job_steps` records that a stage ran and when, but not which
asset it ran for and not what it spent — so "what does transcription cost per
source hour" and "which stage is the money" are questions the schema cannot
answer, and C1 cannot price a credit without them.

## What is added, and why each column rather than a derivation

**`job_steps.asset_id`** — the runner already tracks it (`StepRun.asset_id`) and
`upsert_step` dropped it on the floor. The per-asset phase runs six stages per
upload, so without it a three-upload job's timings are eighteen rows that cannot
be told apart, and a per-source-hour baseline is not computable at all.

**`job_steps.seconds` and `cumulative_seconds`** — duration looks derivable from
`finished_at - started_at`, and it is not. `upsert_step` is idempotent on
`(job_id, idx)`: a retry overwrites the row, so the derived figure describes the
last attempt only. A stage that failed twice at eight minutes and succeeded on
the third reads as one cheap stage. `seconds` is the attempt that ended the
step; `cumulative_seconds` is every attempt including the failed ones, which is
what a retry actually costs.

**`job_steps.from_cache`** — the ingest cache is why a re-run performs zero
transcription (ADR-0016). Today it survives only as a `cached_assets` count in
one aggregate log line, so a trace of a re-run shows six stages that took no
time and no reason for it. A stage that was served rather than executed has to
say so, or the baseline averages cache hits into the cost of doing the work.

**`job_steps.model_cost_micros`** — micros, not cents. A scoring call costs a
fraction of a cent and integer cents rounds it to zero; summing zeros is how a
cost model concludes the models are free. `bigint` because micros of dollars at
job scale is nowhere near its range and the alternative is a float, which does
not belong in money.

**`job_llm_calls`** — one row per model call, which is the only shape that
answers "per model" as the workstream requires. `Ledger`/`CallRecord` already
carry exactly these fields in memory (`llm/base.py`) and write them into the
job's `.mishne.json`; this is that record, kept somewhere queryable.

`fell_back_from` is on it deliberately. `llm/router.py` fails over across
vendors, and a failover is one call that succeeded, not two calls of which one
failed. Without the column, the obvious error-rate query counts it as a failure
and reports a problem that is the system working.

No column here carries customer content — task name, vendor, model id, token
counts, latency, cost, status. Prompts and completions are not recorded and must
not be added (docs/architecture/04-security.md).

## Expand only

Every column added to `job_steps` is nullable or defaulted, so release N — which
does not know they exist — keeps inserting. `job_llm_calls` is new, so NOT NULL
is correct on its columns for the reason `create_org_table` documents. Nothing
is renamed, no constraint is tightened, and `jobs.cost_cents` is left exactly as
0001 declared it; this migration gives it a writer, not a new shape.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from conventions import concurrent_index, create_org_table, drop_concurrent_index

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    # ── job_steps: which asset, how long, and what it spent ────────────────
    op.add_column("job_steps", sa.Column("asset_id", sa.Text(), nullable=True))
    op.add_column("job_steps", sa.Column("seconds", sa.Float(), nullable=True))
    op.add_column(
        "job_steps", sa.Column("cumulative_seconds", sa.Float(), nullable=True)
    )
    op.add_column(
        "job_steps",
        sa.Column(
            "from_cache",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "job_steps",
        sa.Column(
            "model_cost_micros",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    op.execute(
        "COMMENT ON COLUMN job_steps.asset_id IS "
        "'The upload this step ran for; NULL for a job-phase step. Not a "
        "foreign key: a step record describes what happened and must survive "
        "the asset being purged by retention.'"
    )
    op.execute(
        "COMMENT ON COLUMN job_steps.seconds IS "
        "'Duration of the attempt that ended this step. Not derivable from "
        "finished_at - started_at, which a retry overwrites.'"
    )
    op.execute(
        "COMMENT ON COLUMN job_steps.cumulative_seconds IS "
        "'Every attempt, failed ones included. What a retry actually cost.'"
    )
    op.execute(
        "COMMENT ON COLUMN job_steps.from_cache IS "
        "'The step was served from the ingest cache rather than executed "
        "(ADR-0016). A cache hit must not be averaged into the cost of the "
        "work it skipped.'"
    )
    op.execute(
        "COMMENT ON COLUMN job_steps.model_cost_micros IS "
        "'Model spend attributed to this step, in millionths of a dollar. "
        "Micros because a scoring call rounds to zero cents.'"
    )

    # ── one row per model call ─────────────────────────────────────────────
    create_org_table(
        "job_llm_calls",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "job_id",
            sa.Text(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The step this call belongs to, by pipeline order. Not a foreign key to
        # job_steps: that table's key is (job_id, idx) and duplicating the pair
        # is cheaper than a composite reference nothing else needs.
        sa.Column("step_idx", sa.Integer(), nullable=False),
        sa.Column("step_name", sa.Text(), nullable=False),
        # brief | propose | score — the stage's own name for the work, which is
        # what makes "which model is better at proposals" a query (ADR-0011).
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("cost_micros", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        # An unpriced model is not a free model. NULL cost_micros with priced
        # false is UNKNOWN; 0 with priced true is genuinely nothing. A billing
        # path that cannot tell them apart under-charges silently (llm/catalog).
        sa.Column("priced", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        # The model this call was a failover FROM. Non-empty means the router
        # moved vendors and this call is the recovery, not a second failure.
        sa.Column("fell_back_from", sa.Text(), nullable=False, server_default=sa.text("''")),
        # Proposals the silence gate refused, over proposals made. The direct
        # measure of whether a model can hold a hard constraint (ADR-0010).
        sa.Column("violations", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("proposals", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # The error TYPE, never its message: a provider echoes the prompt back
        # in an error string often enough to make that a content leak.
        sa.Column("error_type", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )

    op.execute(
        "COMMENT ON TABLE job_llm_calls IS "
        "'One row per model call. No prompts, no completions, no customer "
        "content of any kind — see docs/architecture/04-security.md.'"
    )

    # "What did this job spend" is the read on every job page; "what does this
    # model cost us" is the read C1 prices from. Built plainly rather than
    # CONCURRENTLY: the table is created three statements above this one, so
    # there is nothing to lock out and no reason to leave the transaction.
    op.create_index("ix_job_llm_calls_job", "job_llm_calls", ["job_id", "step_idx"])
    op.create_index("ix_job_llm_calls_model", "job_llm_calls", ["org_id", "model"])

    # job_steps, however, exists and is being written to. Per-asset timings for
    # a stage across jobs is the transcription baseline, and building this the
    # blocking way is an outage on the one table every running job writes to.
    concurrent_index("ix_job_steps_name_asset", "job_steps", ["org_id", "name", "asset_id"])


def downgrade() -> None:
    drop_concurrent_index("ix_job_steps_name_asset", "job_steps")
    op.drop_index("ix_job_llm_calls_model", table_name="job_llm_calls")
    op.drop_index("ix_job_llm_calls_job", table_name="job_llm_calls")
    op.drop_table("job_llm_calls")

    for column in (
        "model_cost_micros",
        "from_cache",
        "cumulative_seconds",
        "seconds",
        "asset_id",
    ):
        op.execute(f"COMMENT ON COLUMN job_steps.{column} IS NULL")
        op.drop_column("job_steps", column)
