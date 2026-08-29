# B3 — Orchestration: Step Functions, workers, the step contract

> Thread starter. Read [../HANDOVER.md](../HANDOVER.md) first; you should not
> need any other file.

## Goal

Turn `run.py` — twelve stages called in sequence in one process — into a durable
distributed workflow that survives worker death, retries the right things, and
reports progress to the UI.

## What already exists

- **The whole pipeline, working**, in `apps/api/src/mishne/pipeline/steps/`. One
  file per stage, each already a pure-ish function over explicit inputs.
- `apps/api/run.py` — the orchestration as it is today. It is the specification:
  whatever the workflow does must produce the same artifacts.
- `pipeline/project.py` — per-asset ingest with a disk cache and `CACHE_VERSION`.
  It already encodes the correct seam: stages 0-4 are **per asset, cached
  forever**; 5-8 are per job across chosen assets; 9-12 map back per asset.
- `apps/web/src/components/job-stages.tsx` — the progress UI, against fixtures.
- ADR-0002 chose a workflow engine over an agent framework, and
  [../architecture/00-overview.md](../architecture/00-overview.md) sets the step
  contract: `(job_id, step_input_ref) → step_output_ref`.

## What to build

1. A Step Functions state machine mirroring the stages, with the per-asset
   ingest as a Map state over the job's assets.
2. Workers: containers with ffmpeg, the models, and the pipeline package. Sizing
   is driven by transcription — see traps.
3. The step contract as an actual interface: every step reads a ref, writes a
   ref, and is idempotent on `(job_id, step, attempt)`.
4. Retry policy per stage. Transcription and model calls are retryable;
   assembly and validation failing means something is genuinely wrong and should
   surface, not spin.
5. Progress events into the jobs table so `job-stages.tsx` can show real state.
6. Cancellation, and a credit refund path when a job is cancelled or fails after
   the hold (ADR-0006).

## Decisions already made

- **A workflow engine, not an agent framework** (ADR-0002). The pipeline is a
  fixed DAG with a deterministic core; an agent choosing the next step adds
  nondeterminism to the one part of the system that must not have any.
- **Step Functions over Temporal for the MVP** — less to operate, and the DAG is
  simple. The step contract is the abstraction that makes this reversible.
- **Stages 0-4 are cached per asset, forever.** Re-running a job must never
  re-transcribe. This is what makes "add a fourth reel and re-cut" cheap and is
  the economics of the whole multi-upload feature (ADR-0008).
- Stage 8 selection (CP-SAT) and stages 9-12 are **deterministic** — given the
  same inputs they produce byte-identical outputs, which is how re-runs are
  verified.

## Decisions still open

- GPU or CPU for transcription. Everything so far is CPU faster-whisper. Cost per
  job is the deciding number and nobody has measured it at scale.
- Whether span proposal (stage 6) fans out — it is one model call per long beat,
  35 on a 26-minute interview, and they are independent.
- Batch API for scoring, which is materially cheaper and slower, and whether the
  product can accept the latency.

## Traps

- **large-v3 peaks around 4.6 GB.** A 4 GB container gets an OOM kill with exit
  137 and no useful message. Size workers accordingly or use a smaller model and
  measure the quality cost.
- **Models must be baked into the worker image.** A cold start that pulls 3 GB
  from Hugging Face is not a cold start you want against a job SLA — and the
  network may not even allow it.
- **Python must be 3.9-3.13.** OTIO has no 3.14 wheel and fails with
  `RuntimeError: bad any cast`. `apps/api/setup.sh` already pins this.
- ffmpeg and ffprobe must be in the image. `setup.sh` checks for them.
- **The ingest cache is versioned for a reason.** If a worker deploys new
  segmentation code against an old cache, it serves beats built by code that no
  longer exists. Bump `CACHE_VERSION` in `pipeline/project.py` on any change that
  affects ingest output.
- Model calls now go through `llm/router.py`, which already fails over across
  vendors. Do not add a second retry layer on top without checking you are not
  multiplying attempts.

## Definition of done

- A job submitted through the API runs end to end on workers and produces the
  same four artifacts as `run.py` on the same input, byte-identical for the
  deterministic stages.
- Killing a worker mid-job resumes without losing completed stages.
- Re-running a job with an unchanged asset performs **zero** transcription.
- Progress appears in the UI stage by stage.
- Cancelling a job releases the credit hold.
