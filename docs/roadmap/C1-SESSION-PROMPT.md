# C1 — session prompt

Requires B1 (persistence), B3 (orchestration) and B4 (auth). The ledger is
already wired; this is the payment provider and the pricing.

```
We are working on mishne.ai. This session has one job: workstream C1 — billing
with real money. Stripe, and the cost numbers that decide what a credit is
worth.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                     — what exists, how to run it, the traps
  docs/roadmap/C1-billing-live.md      — this workstream's brief
  docs/adr/0006-credit-hold-settle-ledger.md
                                       — hold, settle, and why the ledger is
                                         append-only

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

C1 only. Do not touch the orchestrator, the web screens (C2) or retention (C4).

Phase A (selection corpus, Avid acceptance) is deferred by decision.

## What already exists — do not rebuild it

  db/jobs.py                   hold / settle / release / purchase, against the
                               append-only credit_ledger, with org_balances
                               written in the same transaction as its projection
  routers/jobs.py              recomputes the estimate, refuses a stale cap,
                               holds at submission, releases on cancel
  orchestration/worker.py      settles at min(actual, approved_cap) on success,
                               releases the whole hold on failure
  tests/test_jobs_api.py       all of the above, including that a hold and its
                               release net to zero in the ledger
  billing/credits.py           tiers, rates, packs, estimate_job — pure
  llm/router.py                estimate() prices model spend for a job before it
                               runs, using the model that will actually run it
  stripe_events                a table for webhook dedupe that nothing writes to

The lifecycle is DONE. What is missing is the money coming in, and knowing what
the work costs.

## What to build

1. Stripe checkout for the packs, the webhook, receipts. Credits are granted on
   the WEBHOOK, never on the redirect — a customer who closes the tab must not
   lose their purchase.
2. Dedupe on the Stripe event id. Append-only is not the same as idempotent.
3. A real cost model: model spend is exact already; transcription and compute
   are the larger half and nobody has measured them. Getting that number is part
   of this workstream now.
4. Per-project credit aggregation and the UI number.
5. The multi-asset price. Both the estimate and job submission still price on
   the FIRST asset only — a job drawing on three uploads is priced on one.
6. Low-balance warnings.

## Decisions I have already made — do not relitigate

- Append-only ledger, enforced by a trigger. A correction is a new entry.
- Hold at submission, settle at min(actual, cap). The customer is never charged
  more than they approved, even when the job costs more. That risk is ours.
- The API recomputes every price; a client-supplied figure is only ever compared
  against it.
- Charged on source hours, not machine time. How long our workers take is ours
  to improve.
- A failed job costs the customer nothing.

## Decisions still open — raise them, do not quietly pick one

- What a credit is worth, and the margin. Needs the numbers from (3).
- Whether the quality/cost routing policy becomes a customer-visible choice.
- What deleting a customer means for an append-only ledger. C4 has the same
  question about the audit log and the answer has to be the same one.

## Traps

- Charging above an approved estimate is the fastest way to lose a broadcast
  customer. min(actual, cap) is load-bearing.
- An unpriced model records cost as UNKNOWN, never zero (llm/catalog.py). A
  billing path that treats unknown as free under-charges silently.
- The catalog prices go stale. They are data in llm/models.json for that reason;
  if billing depends on them, something has to refresh and verify them.
- The balance is a projection. A new write path must write org_balances in the
  same transaction as the ledger entry, or the two disagree and only one is true.
- Stripe test mode and live mode have different keys and different webhook
  secrets. A test-mode purchase granting real credits is a bug you find in
  production.

## Definition of done

- A pack bought with a test card moves the balance, via the webhook.
- The same webhook replayed twice grants credits once.
- A multi-asset job is priced on all of its assets.
- The estimate is within a documented margin of what is charged, measured over
  at least twenty real jobs.
- A ledger export reconciles to the Stripe balance.
- The existing tests still pass.

## Environment

  docker compose -f infra/docker-compose.yml up -d   local Postgres
  cd apps/api && ./setup.sh                          venv, checks
  .venv/bin/alembic upgrade head

Start by showing me the cost of one real job, broken down — model spend from the
router's ledger, and worker time from job_steps. Everything else in this
workstream is priced off that number and it does not exist yet.
```
