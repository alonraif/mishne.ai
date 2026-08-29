# ADR-0010 — Selection chooses spans, and every boundary is gated on silence

**Status:** Accepted · **Date:** 2026-08-29

## Context

The beat was the atomic unit of selection. Stage 6 scored beats, the solver
picked whole beats, and nothing downstream was ever offered a boundary that
stage 4 had not produced.

On a 26-minute English interview asked for a two-minute cut, that produced 55
beats with a median of 30 seconds, 64% of the running time in beats over 30
seconds, and a cut that was four blocks. One selected block ran 47.8 seconds and
held two unrelated things: the end of an answer, and the interviewer starting a
new question.

The seams were not there to find. That interview has 55 silences over 600 ms and
17 over a second in its whole length; sweeping the beat-pause threshold from
1200 ms to 600 ms moved the median beat to 21 s and left half the running time
still in beats over 30 s. No threshold fixes it.

An editor's response to a 47-second block is to cut inside it. That move did not
exist in the architecture, and adding a language model would not have created
it — a model that scores beats still returns a score for a 47-second beat.

## Decision

A new stage 6 proposes **candidate spans**: narrower versions of a beat that may
begin or end inside a sentence. They are `Beat` objects carrying `parent_id` and
`kind`, so scoring, selection, refinement and assembly treat them as ordinary
beats. The original beat is always among the candidates.

**Every proposed boundary is gated on real silence**, computed from the VAD
before anything is proposed. A boundary that cannot be paid for is refused —
including one a language model asked for, which is dropped rather than snapped
to a nearby legal point.

The solver gains a **non-overlap constraint**: two candidates covering the same
source time cannot both be selected.

The proposer is an interface. The control enumerates spans between legal points;
the Claude proposer is asked which spans are coherent thoughts.

## Rationale

- **The gate is the stage, not a detail of it.** A span whose endpoints have no
  silence behind them clips the speech, which is the most audible failure a cut
  has, and no rationale makes it audible. Gating deterministically before the
  model's output is trusted keeps the guarantee independent of the model.
- **Dropping, not snapping, an illegal proposal.** Snapping would move the cut
  off the thought the rationale describes, producing a cut that is defensible on
  paper and wrong in the room.
- **Non-overlap is newly necessary.** Beats never overlapped, so the constraint
  never existed. Candidates do, and selecting two would play the same seconds
  twice. The test is on time rather than parentage, because disjoint spans of
  one long answer — keep the opening and the payoff, drop the middle — are a
  real edit worth allowing.
- **Spans are Beats.** A separate type would have meant touching scoring,
  selection, refinement, assembly and the transcript page. Provenance fields on
  the existing type cost two fields and no new code paths.
- **The transcript page lists beats, not candidates.** 232 candidates from 61
  beats would show an editor the same material six times. The page shows what
  was considered and, on a row that was carved, the words that actually made it.

## Consequences

- Measured on the reference interview: 232 candidates from 61 beats, and a
  120-second cut of 14 clips with a median of 10.2 s, against 7 clips including
  two of 47.8 s and 38.4 s. No overlapping source ranges; all four artifacts
  validate.
- Already-cut material barely changes — the Hebrew AAF carved 2 spans from 23
  beats, because seam-derived beats are already editorial units. The stage earns
  its cost exactly where beats are coarse.
- The control proposer explicitly has no judgement, and says so in the run
  output. It offers "of upbeat and creative as much as I can from home" as
  readily as a whole sentence. **The machinery is verified; the choices are
  not.**
- Cost: one model call per long beat, on top of scoring.

## Alternatives considered

**Lower the pause threshold.** Measured and rejected above.

**Sentence-level beats everywhere.** Would fragment interview answers, where a
whole answer is the right unit and half of one is a non-sequitur. Spans get the
granularity without giving up the default.

**Let the model return timecodes directly.** Rejected: it puts the model in
charge of frame accuracy, which stages 9 and 10 already guarantee
deterministically. Indices into a legal set keep that guarantee.

## Open

**There is no ground truth.** Every threshold here — 300 ms of silence, 12 s
before carving, 2 s minimum span — was chosen by inspection of two clips. The
editor's own EDL for a graded interview would say how often real cuts land
inside sentences and which silences they use. That is the same gap Spike B has,
and the same corpus would close both.
