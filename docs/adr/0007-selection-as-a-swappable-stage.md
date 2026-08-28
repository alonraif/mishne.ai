# ADR-0007 — Selection is a swappable stage

**Status:** Accepted · **Date:** 2026-08-28

## Context

Not every user wants a machine choosing their soundbites. Many editors do want a
frame-accurate transcript they can cut on rather than scrubbing three hours of
rushes. Others want a starting point they can adjust.

The question is whether supporting manual and hybrid editing means a second
pipeline.

## Decision

It does not. **Stage 7 (selection) has a single, narrow output contract — an
ordered list of beats — and anything that can produce that list can stand in for
it.** A CP-SAT solver produces it in AI mode; a person produces it in manual
mode; a person editing the solver's output produces it in hybrid mode.

Three job modes, one pipeline:

- `ai` — stages 0–12 run start to finish
- `hybrid` — stages 0–8 run, the job parks in `awaiting_edit`, the user's cut
  resumes at stage 9
- `manual` — stages 0–4 run, the job parks, the user's cut resumes at stage 9

User-authored cuts enter through `POST /jobs/{id}/cut`.

## Rationale

This is the payoff of the staged design in
[01 — Edit Engine](../architecture/01-edit-engine.md) and the step contract in
[ADR-0002](0002-workflow-engine-not-agent-framework.md). Because every stage is a
pure function with an explicit input and output reference, substituting one
stage's producer is a routing decision rather than an architectural change.

**Cut-point refinement must still run** in every mode. The user picks *what*;
stage 9 decides *where* — snapping to silence, adding handles, refusing to cut
inside a word, quantizing to frame boundaries. A person marking text should not
have to reason about frame accuracy, and should not be able to produce a cut that
clips a consonant. Keeping stage 9 mandatory is what makes a hand-marked cut as
technically sound as a generated one.

**Manual mode is not charged for the LLM stages** because they do not run. See
[06 — Billing](../architecture/06-billing-and-metering.md).

## Consequences

**Positive**

- Three products from one pipeline.
- Manual mode answers the "I don't trust AI selection" objection without
  argument, and still sells transcription and assembly.
- **Hybrid produces the quality metric for free.** The diff between the proposed
  cut and the shipped cut is exactly the overlap measure Spike B defines, and it
  arrives on every job rather than in a one-off study. Instrument it from the
  first hybrid job — this is the most valuable telemetry in the system.

**Negative**

- A new non-terminal state, `awaiting_edit`, with no timeout and an indefinite
  lifetime.
- Credit holds outlive the pipeline run; hold expiry needs deciding.
- Retention must count from completion rather than upload, or a parked job's
  source media gets purged out from under it.
- The transcript page becomes an interactive editor, which is a materially larger
  piece of frontend than a read-only view.
