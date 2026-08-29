# C1 — Billing, for real money

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Customers pick a tier at signup, buy credits in packs, see an estimate before
each job and approve it, and get charged what the job actually used.

## What already exists

- `apps/api/src/mishne/billing/credits.py` and `ledger.py` — the hold/settle
  model in code, unwired to any payment provider.
- `packages/shared/src/billing.ts` — tiers, pack sizes, estimate shape.
- `apps/web/src/app/(app)/billing/page.tsx`, `components/credit-meter.tsx`,
  `components/new-job-flow.tsx` — the UI including the pre-submit approval step.
- `routers/billing.py` — endpoint shapes against mocks.
- **`Router.estimate()` in `llm/router.py`** — prices the model spend for a job
  before it runs, using the model that will actually run it. Measured on a
  26-minute interview: $0.24-$0.50 depending on policy.
- ADR-0006 — the ledger design.

## What to build

1. Stripe: checkout for credit packs ($50 / $100 / $200), webhooks, receipts.
2. Wire the ledger: a hold at job submission, settled on completion, released on
   cancellation or failure.
3. A real estimator. Model spend is now computable exactly; transcription and
   compute are the larger and currently unmeasured half — get those numbers from
   B3 before quoting anything.
4. Credits tracked per project as well as per org, which is what was asked for.
5. Low-balance warnings, and a job that cannot start because the balance will not
   cover the estimate.

## Decisions already made

- **Append-only ledger.** No row is ever updated; a correction is a new entry.
- **Hold then settle**, charging `min(actual, cap)`. The customer approved a
  number and must never be charged more than it, even when the job costs more.
  That risk sits with the business, which is the correct place for it.
- The estimate is shown and **explicitly approved** before submission.
- Credits are tracked per project, because media projects are long-running and
  budgets are per production.

## Decisions still open

- What a credit is worth, and the margin over cost. Needs the compute numbers
  from B3.
- Whether a failed job costs the customer anything. Recommendation: no, and
  absorb it — a customer charged for a failure churns.
- Whether the `quality`/`cost` routing policy is exposed to the customer as a
  price/quality choice. It maps naturally onto tiers.

## Traps

- **`min(actual, cap)` is load-bearing.** Charging above an approved estimate is
  the fastest way to lose a broadcast customer.
- Stripe webhooks arrive more than once. The ledger being append-only does not
  make it idempotent — key on the Stripe event id.
- An unpriced model records cost as **unknown**, never zero (`llm/catalog.py`).
  A billing path that treats unknown as free will under-charge silently. Decide
  what to do when a job used an uncatalogued model.
- The model catalog prices go stale — they are data in `llm/models.json` for
  that reason. If billing depends on them, something must refresh and verify
  them, or margins drift without anyone noticing.

## Definition of done

- A customer buys a pack with a real card in test mode and the balance moves.
- Submitting a job holds credits; completing settles; cancelling releases.
- The estimate shown before approval is within a documented margin of what is
  actually charged, measured across at least twenty jobs.
- A ledger export reconciles to the Stripe balance.
