# ADR-0004 — Deterministic constraint solver for segment selection

**Status:** Accepted · **Date:** 2026-08-28

## Context

The core task is choosing which of several hundred transcript beats make the cut,
subject to a hard duration target. The obvious approach is to ask an LLM: *here are
the beats, here is the brief, give me ten minutes of the best material.*

## Decision

**The LLM scores. A constraint solver selects.**

Selection is modelled as constrained optimization and solved with OR-Tools CP-SAT:
maximize total weighted beat value subject to duration bounds, forced inclusions and
exclusions, dependency closure, redundancy clusters, minimum segment duration, and
speaker balance.

## Rationale

**Language models cannot reliably hit a duration target.** Ask for exactly ten
minutes and the result is seven or fourteen. Duration is the one constraint the
customer specified explicitly and the one most visible when violated.

Beyond duration, the constraints are genuinely combinatorial: dependency closure
(selecting a payoff requires its setup), redundancy exclusion (at most one beat per
near-duplicate cluster), and speaker balance interact in ways that need a solver, not
a plausible-sounding narrative about why these particular beats were chosen.

The problem is small — a few hundred beats — so CP-SAT solves it optimally in well
under a second.

This split plays to each component's strength. Scoring a passage for narrative value
is judgment, and the LLM is good at it. Optimizing a weighted selection under hard
constraints is arithmetic, and the solver is good at it and the LLM is not.

## Consequences

**Positive**

- Duration target is met exactly, every time.
- Deterministic: identical scores yield an identical cut. Reproducibility becomes a
  property of the scoring cache rather than of model sampling.
- Constraints are declarative and testable in isolation.
- New constraints — minimum gap, maximum consecutive beats from one speaker, per-
  source balance for multicam — are additions to a model, not prompt engineering.

**Negative**

- Score calibration matters more. A solver optimizes exactly what it is given, so
  miscalibrated scores produce confidently wrong selections. Requires a scoring
  rubric with worked examples and periodic evaluation against human cuts.
- Adds OR-Tools as a dependency and constraint modelling as a skill on the team.
- The solver has no sense of narrative flow. This is why Stage 8 exists: the LLM
  reviews the assembled sequence and returns targeted revisions, which are fed back
  as additional constraints and re-solved.
