# C3 — session prompt

Requires B3 (orchestration). The step timings exist; nothing reads them.

```
We are working on mishne.ai. This session has one job: workstream C3 —
observability. What a job costs, where it spends its time, and knowing it failed
before the customer says so.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                     — what exists, how to run it, the traps
  docs/roadmap/C3-observability.md     — this workstream's brief
  docs/architecture/04-security.md     — the logging rule, which is absolute

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

C3 only. Billing is C1 — but C1 cannot price a credit without the cost number
this workstream produces, so that number is the priority.

Phase A (selection corpus, Avid acceptance) is deferred by decision.

## What already exists — do not rebuild it

  logging.py                   structured logging, and `scrub`, which blocks
                               customer content BY KEY. It is the enforcement
                               point; do not route around it
  llm/base.py                  every model call already recorded: task, provider,
                               model, latency, tokens, cost, whether it parsed
  job_steps                    per stage per asset: status, attempt, started_at,
                               finished_at, and a detail string
  jobs.cost_cents              a column nothing populates — the router's ledger
                               knows the number and the worker does not save it
  <name>.mishne.json           the per-job record: modelVersions, llmCalls, cost

## What to build

1. OpenTelemetry traces, one span per stage, correlated by job_id. The step
   boundaries are already in the runner; this is instrumentation.
2. Cost per job, persisted: model spend into jobs.cost_cents, worker time from
   the step timings.
3. Alerting on what hurts: a job that failed after its retries, a step whose
   duration leaves its distribution, spend-per-job moving, a growing queue.
4. A transcription cost baseline per source hour, which is what decides the open
   GPU-or-CPU question.
5. Log retention and access.

## Decisions I have already made — do not relitigate

- NO CUSTOMER CONTENT IN LOGS. Ever. No transcript text, no filenames, no brief
  text, no paths. IDs, durations, counts, status. This is why step.failed logs
  an exception's TYPE and not its message.
- The same rule governs job_steps.detail, which is rendered in the UI and kept
  for the life of the job.
- Structured logs, not printf. run.py's console output is a CLI affordance.

## Decisions still open — raise them, do not quietly pick one

- Vendor. Anything OTel-compatible.
- Whether per-customer cost is shown to the customer.
- Sampling. 100% is affordable now and will not stay that way.

## Traps

- `scrub` blocks by KEY. A new key carrying customer text passes straight
  through. Add the key.
- llm/router.py already fails over across vendors. Counting a failover as two
  failures gives an error rate that is wrong in the direction that causes
  needless work.
- Cost is not duration. Transcription dominates wall-clock; model calls dominate
  spend. Optimising the one you can see is how the bill stays where it is.
- The ingest cache means a re-run is fast for a reason. A trace that does not
  show the cache hit makes it look like the work vanished.

## Definition of done

- A job's trace shows every stage, with cache hits visible.
- Cost per job is queryable per stage and per model, and C1 can price from it.
- A failed job pages somebody; a job that merely retried does not.
- No customer content in the telemetry, tested rather than assumed.

## Environment

  docker compose -f infra/docker-compose.yml up -d
  cd apps/api && ./setup.sh && .venv/bin/alembic upgrade head

Start by giving me the cost and the time breakdown of one real job from what is
already recorded. If that is hard to produce, the instrumentation gap is exactly
there.
```
