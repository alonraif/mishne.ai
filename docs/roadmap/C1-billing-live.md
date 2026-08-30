# C1 — Billing, for real money

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Customers buy credits with a card, and the numbers they are charged are numbers
somebody has checked against what the work actually cost.

## What already exists — and it is more than this brief originally assumed

B3 built the ledger. Do not rebuild it:

- **`db/jobs.py`** — `hold`, `settle`, `release`, `purchase`, against the
  append-only `credit_ledger` with `org_balances` written in the same
  transaction as its projection. Idempotency is the unique index on
  `(job_id, kind)`: a retried settle is a duplicate-key error, not a double
  charge.
- **The lifecycle is wired.** `POST /v1/jobs` recomputes the estimate, refuses a
  cap that no longer matches, and holds. `POST /v1/jobs/{id}/cancel` releases in
  full. `orchestration/worker.py` settles at `min(actual, approved_cap)` on
  success and releases on failure. `tests/test_jobs_api.py` covers all of it,
  including that a hold and its release net to zero in the ledger.
- **A job that the balance will not cover is a 402** with the shortfall in the
  message.
- `billing/credits.py` — tiers, rates, packs, `estimate_job`. Pure functions;
  no database. (`billing/ledger.py` was an in-memory model of the same rules
  from before the database existed, and is deleted — two descriptions of one
  contract is how one of them stops being true.)
- `Router.estimate()` in `llm/router.py` prices model spend for a job before it
  runs, using the model that will actually run it: $0.24-$0.50 on a 26-minute
  interview depending on policy.
- `apps/web/.../billing/page.tsx`, `credit-meter.tsx`, `new-job-flow.tsx` — the
  UI including the pre-submit approval step, still against fixtures.
- ADR-0006 — the design, and it is what the above implements.

## What to build

1. **Stripe.** Checkout for the packs ($50 / $100 / $200), the webhook, receipts.
   `stripe_events` exists as a table for dedupe and nothing writes to it;
   `db/jobs.purchase()` is the credit half and takes a `stripe_event_id`.
2. **Credits granted on the webhook, never on the redirect.** A customer who
   closes the tab must not lose their purchase.
3. **A real cost model.** Model spend is computable exactly. Transcription and
   compute are the larger half and are still unmeasured — B3 built the worker
   but nothing has run at scale, so the number does not exist yet. Getting it is
   part of this workstream now, not an input to it.
4. **Per-project credit tracking.** `credit_ledger.project_id` is populated and
   is a plain column (migration 0004); the aggregation and the UI are missing.
5. **Low-balance warnings**, and the multi-asset price. `estimate` and job
   submission both still price on the first asset only; a job drawing on three
   uploads is priced on one of them, which is wrong in the customer's favour
   and will stop being funny at scale.

## Decisions already made

- **Append-only ledger.** No row is ever updated; a correction is a new entry.
  Enforced by a trigger, not by convention.
- **Hold then settle**, charging `min(actual, cap)`. The customer approved a
  number and is never charged more, even when the job costs more. That risk sits
  with the business, which is the correct place for it.
- The estimate is shown and **explicitly approved** before submission, and the
  API recomputes it rather than trusting the client's figure.
- Credits are tracked per project as well as per org, because media projects run
  for months and budgets are per production.
- **Charged on source hours, not on machine time.** How long our workers take is
  ours to improve; billing for it would mean a slow release costs the customer.

## Decisions still open

- What a credit is worth, and the margin over cost. Needs (3).
- Whether a failed job costs anything. Recommendation: no, and absorb it — a
  customer charged for a failure churns. That is what the code does today.
- Whether the `quality`/`cost` routing policy is exposed as a price/quality
  choice. It maps naturally onto tiers.
- **What deleting a customer means for the ledger.** It is append-only and
  refuses deletion; C4 has the same question about the audit log. Whatever is
  decided has to be the same answer.

## Traps

- **`min(actual, cap)` is load-bearing.** Charging above an approved estimate is
  the fastest way to lose a broadcast customer.
- Stripe webhooks arrive more than once. Append-only does not mean idempotent —
  key on the Stripe event id, which is why `stripe_events.id` *is* that id.
- An unpriced model records cost as **unknown**, never zero (`llm/catalog.py`).
  A billing path treating unknown as free under-charges silently.
- The model catalog prices go stale — they are data in `llm/models.json` for
  that reason. If billing depends on them, something has to refresh and verify
  them, or margins drift with nobody noticing.
- **The ledger's balance is a projection.** If you add a write path, write the
  `org_balances` row in the same transaction as the entry, or the two disagree
  and only one of them is the truth.

## Definition of done

- A customer buys a pack with a test card and the balance moves, via the webhook.
- Replaying the same webhook twice grants credits once.
- A multi-asset job is priced on all of its assets.
- The estimate shown before approval is within a documented margin of what is
  actually charged, measured across at least twenty real jobs.
- A ledger export reconciles to the Stripe balance.
- The existing tests still pass.
