# ADR-0016 — Resume is idempotent re-execution, not a checkpoint restore

**Status:** Accepted · **Date:** 2026-08-29

## Context

B3 turns a script that runs twelve stages in one process into a job that
survives the machine running it. "Survives" needs a definition, because the
obvious one is expensive and the useful one is not.

A checkpoint restore — serialise the pipeline's state after each stage, reload
it on the next worker — is what "resume" usually means. Here it would mean
serialising a solver's input, an OTIO timeline and a set of candidate spans
between two releases that, under ADR-0012, may be different releases: an
in-flight job's steps were written by the previous version and are read by the
next one. Every one of those objects would become a versioned wire format with
its own compatibility obligations, and the pipeline's internals would stop being
free to change.

The alternative is available because of what the pipeline already is: stages 0-4
are cached per asset on the content hash (ADR-0008), and stages 8-12 are
deterministic — same inputs, identical outputs.

## Decision

**A resumed job is re-entered from the top**, and every stage that is expensive
is served from cache:

* **The per-asset phase** — probe, audio, transcribe, VAD, structure, speakers —
  from the content-addressed ingest cache. A re-run of a job whose assets have
  not changed performs zero transcription.
* **The three model-calling job stages** — brief, propose, score — from JSON
  written into the job's own working directory as each produces its output.
* **Everything from `select` onwards** is recomputed, because it is arithmetic
  and a solver, and recomputing is cheaper than storing.

**Every step is therefore idempotent by construction**, which is the property
the orchestrator actually needs: Step Functions retries, spot interruption and a
worker that dies mid-stage all resolve to "run it again".

**Cancellation is checked between steps, never inside one.** A stage is at most
a few minutes.

## Rationale

- **The expensive thing is transcription**, by an order of magnitude, and it was
  already cached before this workstream existed. Resume that re-transcribes is
  the failure worth preventing; resume that re-runs a CP-SAT solve for a few
  hundred milliseconds is not a failure at all.
- **A serialised checkpoint is a wire format between releases.** Under ADR-0012
  old and new workers run side by side, so every intermediate object would need
  the expand/contract treatment the schema gets. That is a large permanent tax
  on the pipeline's internals to save seconds.
- **Idempotent re-execution is testable without a distributed system.** A test
  runs the same job twice against one work directory and asserts that
  transcription reports `cached`.
- **Killing a stage mid-write is how a half-written artifact reaches a
  customer.** Between-step cancellation costs at most one stage of work.

## Consequences

- **A resumed job repeats its cheap stages**, and its step rows show a second
  attempt. Progress is honest about that rather than pretending the work was
  restored.
- **The two caches are versioned and must be bumped together with the code that
  writes them.** `project.CACHE_VERSION` for ingest, `graph.JOB_CACHE_VERSION`
  for the model stages. A worker running new segmentation code against an old
  cache serves beats built by code that no longer exists, and the only symptom
  is a subtly wrong cut — which is why the version is checked on read and a
  mismatch silently rebuilds.
- **A job's working directory has to outlive one worker** for the model-stage
  caches to help. On a single machine it is a directory; across workers it is
  the derived bucket, which is what `workspace.S3Workspace` mirrors.
- **`review` was deleted rather than left as a stub.** It was designed as a
  coherence pass feeding constraints back to the solver, bounded at two
  iterations, and never built. A registry entry with no implementation becomes a
  state in a generated machine that nothing can run; ADR-0007 makes selection
  swappable, so adding a coherence pass later is a new stage rather than a
  change to any contract — and it should be designed against real cuts once
  there is a corpus to judge them with (A1).
- **AAF cannot be compared byte for byte between runs.** The format carries
  generated MobIDs and a modification date, so two runs of identical code
  produce two different files of identical size. `validate` reading an artifact
  back and comparing it to the timeline is the check that means something; the
  reference-run test compares EDL, FCPXML, OTIO, the transcript and the manifest
  byte for byte, and holds the AAF to validation instead.
