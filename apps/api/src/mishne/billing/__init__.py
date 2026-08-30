"""What a job costs, and what the customer is charged.

Two halves, deliberately in different places:

* **Here: pricing.** Tiers, rates, credit packs, and `estimate_job` — pure
  functions over numbers, with no database and no side effects, so an estimate
  can be computed anywhere and tested without a schema.
* **`db/jobs.py`: the ledger.** Hold, settle, release, purchase — the writes,
  against the append-only table that is the source of truth (ADR-0006).

There was a `billing/ledger.py` modelling the second half in memory, from before
the database existed. It is gone: two descriptions of the same rules are how one
of them quietly stops being true, and the one that mattered was the one keyed on
`(job_id, kind)` in Postgres — where a retried settle is a duplicate-key error
rather than a double charge.
"""

from .credits import (
    ARTIFACT_FLAT,
    CREDIT_PACKS,
    MINIMUM_CHARGE,
    TIERS,
    TRANSCRIPTION_RATE_PER_HOUR,
    Tier,
    estimate_job,
)

__all__ = [
    "ARTIFACT_FLAT",
    "CREDIT_PACKS",
    "MINIMUM_CHARGE",
    "TIERS",
    "TRANSCRIPTION_RATE_PER_HOUR",
    "Tier",
    "estimate_job",
]
