# B3 — session prompt

Requires B1 (persistence) and B2 (storage). The last of the platform pieces.

```
We are working on mishne.ai. This session has one job: workstream B3 —
orchestration. Turn run.py, which calls twelve stages in sequence in one
process, into a durable distributed workflow that survives worker death and
reports progress.

Read these three files first, in this order, and nothing else until you have:
  docs/HANDOVER.md                    — what exists, how to run it, the traps
  docs/roadmap/B3-orchestration.md    — this workstream's brief
  docs/adr/0012-two-environments-and-expand-contract-migrations.md
                                      — in-flight jobs survive deploys

Do not re-derive the architecture or read the whole codebase. The handover is
accurate and current; trust it.

## Scope

B3 only. B1 (persistence) and B2 (storage) are done and you build on them.
Billing, the web app and observability are Phase C.

Phase A (selection corpus, Avid acceptance) is deferred by decision. Nothing
here depends on it.

## What already exists — and one thing that is a trap

  apps/api/run.py              THE SPECIFICATION. Whatever the workflow does,
                               it must produce the same artifacts from the same
                               input. Read it before anything else.
  pipeline/steps/*.py          the twelve working stages, as PLAIN FUNCTIONS
  pipeline/project.py          per-asset ingest with a disk cache and
                               CACHE_VERSION. It already encodes the correct
                               seam: stages 0-4 are PER ASSET and cached
                               forever, 5-8 are per job across chosen assets,
                               9-12 map back per asset.
  apps/web/.../job-stages.tsx  the progress UI, against fixtures

  ** pipeline/steps/__init__.py defines STEPS, and it is STALE. **
  The infra README says the state machine is generated from it. It is not
  safe to do that as written: STEPS omits speakers, aaf_ingest, propose
  (span proposal) and transcript_page, and it lists "review", which is an
  unimplemented stub in review.py. pipeline/steps/base.py defines a Step ABC
  that only review.py subclasses — the twelve working stages never adopted it.

  Reconciling that divergence is the first real task. Do not generate a state
  machine from STEPS until it matches what run.py actually executes.

## What to build

1. A Step Functions state machine mirroring the real stages, with per-asset
   ingest as a Map state over the job's assets.
2. Workers: containers with ffmpeg, the models and the pipeline package.
3. The step contract as an actual interface — every step reads a ref, writes a
   ref, idempotent on (job_id, step, attempt). base.py sketches this; make it
   real or replace it, but do not leave two contracts.
4. Retry policy per stage. Transcription and model calls are retryable;
   assembly and validation failing means something is genuinely wrong and must
   surface rather than spin.
5. Progress events into the jobs tables so job-stages.tsx shows real state.
6. Cancellation, and a credit release when a job is cancelled or fails after
   the hold. (ADR-0006)

## Decisions I have already made — do not relitigate

- A workflow engine, not an agent framework. The pipeline is a fixed DAG with a
  deterministic core; an agent choosing the next step adds nondeterminism to
  the one part of the system that must not have any. (ADR-0002)
- Step Functions over Temporal for the MVP — less to operate, and the step
  contract is what makes that reversible.
- Stages 0-4 are cached per asset, forever. Re-running a job must NEVER
  re-transcribe. That is what makes "add a reel and re-cut" cheap and it is
  the economics of the whole multi-upload feature. (ADR-0008)
- Stage 8 selection and stages 9-12 are deterministic: same inputs, identical
  outputs. That is how a re-run is verified.
- IN-FLIGHT JOBS SURVIVE A DEPLOY. Old and new worker code run side by side
  until work started under the previous release finishes. Step payloads
  therefore carry a version, and a new release must read payloads the old one
  wrote. This is a B3 obligation more than anyone else's. (ADR-0012)

## Decisions still open — raise them, do not quietly pick one

- GPU or CPU for transcription. Everything so far is CPU faster-whisper; cost
  per job is the deciding number and nobody has measured it at scale.
- Whether span proposal fans out. It is one model call per long beat — 35 on a
  26-minute interview — and they are independent.
- Batch APIs for scoring: materially cheaper, materially slower.
- Whether the "review" stage exists at all. It was designed as a coherence pass
  feeding constraints back to the solver, bounded at two iterations, and was
  never built. Either build it or delete it from STEPS and review.py; leaving a
  stub in the registry is how a state machine ends up with a phantom state.

## Traps

- large-v3 peaks around 4.6 GB. A 4 GB container gets an OOM kill, exit 137,
  no useful message.
- Models must be BAKED INTO the worker image. A cold start pulling 3 GB from
  Hugging Face is not a cold start you want against a job SLA, and the network
  may not even allow it.
- Python must be 3.9-3.13. OpenTimelineIO has no 3.14 wheel and fails at import
  with RuntimeError: bad any cast. setup.sh already pins this.
- ffmpeg and ffprobe must be in the image.
- The ingest cache is versioned for a reason. A worker running new segmentation
  code against an old cache serves beats built by code that no longer exists,
  and the only symptom is a subtly wrong cut. Bump CACHE_VERSION in
  pipeline/project.py on any change affecting ingest output — and note this
  interacts directly with rolling deploys.
- Model calls already fail over across vendors inside llm/router.py. Do not add
  a second retry layer on top without checking you are not multiplying
  attempts and cost.

## Definition of done

- A job submitted through the API runs end to end on workers and produces the
  same four artifacts as run.py on the same input, byte-identical for the
  deterministic stages.
- Killing a worker mid-job resumes without losing completed stages.
- Re-running a job with an unchanged asset performs ZERO transcription.
- A job started before a deploy completes correctly after it.
- Progress appears in the UI stage by stage.
- Cancelling a job releases the credit hold.
- STEPS matches what the pipeline actually executes, and there is exactly one
  step contract in the codebase.

## Environment

  docker compose -f infra/docker-compose.yml up -d   local Postgres
  cd apps/api && ./setup.sh                          venv, checks

Reproduce the reference run before changing anything:

  cd samples
  ../apps/api/.venv/bin/python ../apps/api/run.py SyncDaniel.aaf \
    --out /tmp/b3 --replay SyncDaniel_roughcut/work/SyncDaniel_flat_a0.asr.json \
    --target 40s --scorer heuristic --spans enumerate

Expect 23 beats, 4 spans, all four artifacts validating. That output is your
regression target for the whole workstream.

Start by reconciling STEPS with run.py and showing me the diff between what the
registry claims and what the pipeline does. Everything else depends on that
list being true.
```
