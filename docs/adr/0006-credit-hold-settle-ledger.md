# ADR-0006 — Credit ledger with hold and settle

**Status:** Accepted · **Date:** 2026-08-28

## Context

Jobs are paid for with prepaid credits. Cost is not exactly knowable before the
job runs — transcription is billed per minute and token counts vary with content
— but it is closely estimable from source duration.

The naive implementations both fail:

- **A mutable `credit_balance` column, debited on completion.** A user with 5
  credits starts ten concurrent jobs and every one of them runs. Double-charges
  on retry are invisible. Nothing is auditable after the fact.
- **Charging the estimate up front.** Overcharges on every job that comes in
  cheaper than estimated, which is most of them.

## Decision

An **append-only ledger** with a **hold-and-settle** lifecycle. Balance is a
projection of the ledger, never a stored mutable value.

```
submit   → hold(cap)       available -= cap, held += cap
success  → settle(actual)  charge min(actual, cap), release the remainder
failure  → refund(cap)     release the whole hold, charge nothing
cancel   → release(cap)    release the whole hold
```

The user approves the cap before submission. **Settlement charges
`min(actual, cap)`** — the approved figure is a ceiling, not a price.

## Rationale

**Holding at submission** is what makes concurrency limits real. Without it,
credit balance is advisory.

**Capping at the approved figure** removes the main objection to consumption
pricing. The user is never billed more than they agreed to; if the job comes in
under, they keep the difference. Because estimates are conservative by
construction, this costs very little and buys a great deal of trust — which
matters disproportionately with professional buyers who have been burned by
metered cloud services.

**Never charging for a failed job** is not generosity, it is the only defensible
position. This includes jobs the round-trip validation gate rejects: if the AAF
does not open in Media Composer, no value was delivered.

**Append-only** makes the whole thing auditable. "Why is my balance 142.5?" has
an answer that is a query, not an investigation. It also makes idempotency
expressible as a unique constraint rather than as careful code.

**Per-project attribution falls out of `project_id` on ledger entries.** No
separate counter, so nothing can drift from the truth.

## Consequences

**Positive** — concurrency limits enforceable; no bill shock; auditable;
idempotency is a constraint, not a convention; per-project reporting is free.

**Negative**

- Balance is a computed projection, so it must be cached or materialized for hot
  reads. A materialized balance row updated in the same serializable transaction
  as the ledger insert is the usual answer; the ledger stays authoritative.
- Holds can be orphaned if a job is lost without settling. Needs a reconciliation
  job that releases holds for jobs in terminal states.
- Long-lived holds. A hybrid job parked in `awaiting_edit` holds credits until
  the user returns — see [07 — Job Modes](../architecture/07-job-modes.md).
  Hold expiry is unresolved.

## Implementation notes

- Serializable transaction per ledger write.
- Unique constraint on `(job_id, kind)` — a retried `settle` is a no-op.
- Stripe: credits granted on webhook, never on checkout redirect, deduped on
  Stripe event id. A user closing the tab must not lose their purchase.
- `approved_cap` from a client is a claim. Recompute server-side and reject on
  mismatch.
