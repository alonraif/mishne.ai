# C3 — Observability, and the number nobody has

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Know what a job costs, where it spends its time, and that it failed before the
customer tells you.

## What already exists

- `logging.py` — structured logging, and a `scrub` processor that blocks
  customer content by key. It is the enforcement point; do not route around it.
- **`llm/base.py` records every model call**: task, provider, model, latency,
  input and output tokens, cost, whether it parsed, and constraint violations.
  `Ledger.summary()` prints it and every job writes it into `<name>.mishne.json`
  as `llmCalls` and `modelVersions`.
- **B3 put timings in the database.** `job_steps` carries `status`, `attempt`,
  `started_at`, `finished_at` and a `detail` string per stage, per asset. The
  runner logs `step.done` with the same numbers. Cost per stage is one query
  away and nobody has written it.
- `jobs.cost_cents` and `jobs.model_versions` exist as columns. Nothing
  populates `cost_cents` — the router's ledger knows the number and the worker
  does not persist it.
- Stage 12 validation results are per-artifact and structured.
- The transcript page — the customer-facing explanation of the cut.

## What to build

1. **Traces.** OpenTelemetry, one span per stage, correlated by `job_id`. The
   step boundaries already exist in the runner; this is instrumentation, not
   restructuring.
2. **Cost per job, persisted.** Model spend from the router's ledger into
   `jobs.cost_cents`, and worker time from the step timings. This is the number
   C1 needs to set a credit's worth, and it does not exist yet.
3. **Alerting on what actually hurts**: a job that fails after its retries, a
   step whose duration leaves its distribution, a spend-per-job that moves, and
   a queue that is growing.
4. ~~**A per-asset transcription cost baseline**, which decides the open GPU/CPU
   question.~~ **Superseded by ADR-0018.** The question was which fleet to buy,
   and the answer was neither: transcription is a managed API billed by the
   second. Every engine call is a `job_llm_calls` row carrying the audio
   duration it billed for (migration 0006), so cost per source hour is
   `python -m mishne.report --org ... --baseline` rather than a project. What is
   still unmeasured is the same figure on real jobs — the query exists, the
   rows do not, until material runs through with keys set.
5. **Log retention and access**, because the same rule applies to logs as to the
   audit table: they outlive the job and are read by more people than wrote them.

## Decisions already made

- **No customer content in logs. Ever.** No transcript text, no filenames, no
  brief text, no paths. IDs, durations, counts and status. This is why
  `step.failed` logs an exception's *type* and not its message.
- The same rule governs `job_steps.detail`, which is rendered in the UI and
  stored for the life of the job.
- Structured logs, not printf. `run.py`'s console output is a CLI affordance and
  not the logging strategy.

## Decisions still open

- Vendor. Anything OTel-compatible; the cost of the decision is low and the cost
  of not instrumenting is compounding.
- Whether per-customer cost is exposed to the customer. It is a strong trust
  signal and a strong invitation to argue about it.
- Sampling. Job volume is low enough that 100% is affordable today and will not
  stay that way.

## Traps

- **`scrub` blocks by key, not by value.** A new key carrying customer text
  passes straight through it. Add the key.
- The router already fails over across vendors. Counting a failover as two
  failures makes an error rate that is wrong in the direction that causes
  needless work.
- A job's cost is not its duration. Transcription dominates wall-clock and model
  calls dominate spend, and optimising the one you can see is how the bill stays
  where it is.

## Definition of done

- A job's trace shows every stage, with the ingest cache visible as the reason a
  re-run is fast.
- Cost per job is queryable, per stage and per model, and C1 can price from it.
- A failed job pages somebody, and a job that merely retried does not.
- No customer content appears anywhere in the telemetry, tested rather than
  assumed.
