"""Transcription costs money too, and it is billed by the second.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-31

Transcription moved from a self-hosted model to managed engines (ADR-0018), and
with that it became the same kind of thing every other vendor call already is: a
request to a third party with a price on it. `job_llm_calls` is where those
live, and the routed ASR provider now writes one row per engine call — same
step, same asset, same query.

Two columns are needed for that row to mean anything.

**`audio_seconds`** — transcription is billed by duration, not by tokens, so
`input_tokens`/`output_tokens` are zero on these rows and a per-unit cost cannot
be recovered from them. Without a duration, an ASR row says a job spent $0.09
and gives nothing to divide it by. With it, "cost per source hour" — the number
C3 has listed as unmeasured since it was written, and the one the GPU-or-CPU
decision was blocked on — is one query against one table, no join to `assets`,
no dependency on an asset row surviving retention.

It is per call rather than per asset because it has to be: material longer than
an engine's per-request limit is split (`asr/chunking.py`), and each request
bills for the part it saw.

**`cost_estimated`** — one engine bills a flat rate per hour, which applied to a
measured duration is arithmetic the invoice will agree with. The other bills by
token and does not always report token counts, in which case the cost is
published rates applied to *assumed* counts. Those are different claims and the
schema has to hold them apart.

`priced` cannot carry it. `priced=false` already means "this model has no price
in the catalog, treat the zero as unknown". An estimate is the opposite
situation — a price exists, this is our arithmetic against it — and folding the
two together either throws the estimate away or lets it be reconciled against a
real invoice. C1 has already paid for that confusion once: an estimator whose
per-task figures were wrong in opposite directions read as calibrated because
nothing recorded which numbers were measured (`docs/notes/c1-first-cost-numbers`).

## Expand only

Both columns are defaulted, so release N — which does not know they exist —
keeps inserting into this table unchanged. Nothing is renamed, no constraint is
tightened, and no existing column changes meaning: an LLM row written before
this migration is a row with zero audio and an unestimated cost, which is what
it was.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_llm_calls",
        sa.Column("audio_seconds", sa.Float(), nullable=False,
                  server_default=sa.text("0")),
    )
    op.add_column(
        "job_llm_calls",
        sa.Column("cost_estimated", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
    )

    op.execute(
        "COMMENT ON COLUMN job_llm_calls.audio_seconds IS "
        "'Seconds of audio a transcription call billed for; 0 for a language "
        "model call. Cost per source hour is cost_micros over this column, "
        "which is why it is per call and not per asset — long audio is split "
        "and each request bills for the part it saw.'"
    )
    op.execute(
        "COMMENT ON COLUMN job_llm_calls.cost_estimated IS "
        "'The cost is published rates applied to ASSUMED quantities because "
        "the vendor reported none. Priced but not measured — do not reconcile "
        "it to an invoice. Distinct from priced=false, which means no price "
        "exists at all.'"
    )


def downgrade() -> None:
    for column in ("cost_estimated", "audio_seconds"):
        op.execute(f"COMMENT ON COLUMN job_llm_calls.{column} IS NULL")
        op.drop_column("job_llm_calls", column)
