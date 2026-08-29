# C3 — Observability and cost per job

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Know what every job cost, where it spent its time, and why a bad one was bad —
without asking the customer to send you the folder.

## What already exists

- `apps/api/src/mishne/logging.py` — structured logging configuration.
- **`llm/base.py` already records every model call**: task, provider, model,
  latency, input and output tokens, cost, whether it parsed, and constraint
  violations. `Ledger.summary()` prints it and `run.py` writes it into
  `<name>.mishne.json` as `llmCalls` and `modelVersions`.
- Stage 12 validation results are already per-artifact and structured.
- `<name>.mishne.json` per job — the full record of what was decided.
- The transcript page — the customer-facing explanation of the cut.

## What to build

1. Traces spanning the whole job, one span per stage, with the asset and job ids.
2. Cost per job, assembled from the model ledger (exact), transcription compute,
   storage and egress. Model spend is the small half; compute is unmeasured.
3. Quality telemetry that already exists but is not aggregated: proposals refused
   by the silence gate per model, JSON parse failures per model, solver
   infeasibility and greedy fallbacks, cuts that hit the minimum-duration floor
   or were dropped for being too short.
4. Alerting on the things that mean a bad cut shipped: an artifact failing
   validation, a job completing with zero selected beats, a job whose median beat
   is far larger than its target — the run already warns about that last one.
5. A support view: given a job id, everything about it in one place.

## Decisions already made

- **Validation is by independent parse** and runs on every job. A failed
  artifact must never be delivered; the run exits non-zero.
- The model ledger is per call, not per job, so failover and retries are visible.
- `model_versions` records every model that actually ran — a job produced by two
  vendors says so.

## Decisions still open

- Vendor. Anything with traces and structured logs will do; do not over-invest
  before there is traffic.
- Whether to keep the derived audio for support. It makes debugging a bad cut far
  easier and it is customer media, which C4 governs.
- Retention for the per-job JSON, which contains the full transcript.

## Traps

- **The interesting failures are silent.** A job that produces a validated AAF of
  the wrong material fails nothing. The metrics that catch it are editorial —
  beat count, median beat length against target, fraction of the cut coming from
  one beat — not infrastructural.
- The per-job JSON and the transcript page **contain the customer's transcript**.
  They are not debug artifacts to be shipped to a log aggregator without thought.
- Cost per job is dominated by transcription, not by model calls. Measure before
  optimising the visible half.

## Definition of done

- One trace per job, spans per stage, searchable by job id.
- A dashboard showing cost per job broken down, and its trend.
- Alerts on validation failure and on empty or degenerate cuts.
- A support view that answers "why is this cut bad" without a screen share.
