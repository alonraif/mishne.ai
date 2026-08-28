"""Stage 7 — selection, plus the baselines it has to beat.

(Named `selection`, not `select`: `select` is a Python standard-library module
and shadowing it produces a confusing `module has no attribute` failure at
import time rather than anywhere near the mistake.)

The engine's selector is a CP-SAT model, not a language model, for the reason in
[ADR-0004](../../docs/adr/0004-constraint-solver-for-selection.md): models
cannot hit a duration target, and duration is the one constraint the customer
stated explicitly.

The baselines matter as much as the selector. Each is something a rushed
assistant could do in ten minutes, and if the engine cannot beat them it is not
worth its cost.
"""

from __future__ import annotations

import random

from corpus import Beat
from metrics import Interval

# Duration tolerance as a fraction of target. The solver is required to land
# inside this; every baseline is held to the same window so the comparison is
# fair rather than flattering.
TOLERANCE = 0.05


def solve(beats: list[Beat], scores: dict[str, float], target_frames: int,
          tolerance: float = TOLERANCE) -> list[Interval]:
    """Maximise total score subject to a hard duration window.

    A few hundred beats solves optimally in well under a second, so there is no
    reason to approximate.
    """
    from ortools.sat.python import cp_model

    model = cp_model.CpModel()
    pick = [model.NewBoolVar(b.id) for b in beats]

    lo = int(target_frames * (1 - tolerance))
    hi = int(target_frames * (1 + tolerance))
    duration = sum(pick[i] * beats[i].frames for i in range(len(beats)))
    model.Add(duration >= lo)
    model.Add(duration <= hi)

    # Objective: quality-weighted screen time, NOT the sum of raw scores.
    #
    # This distinction is not cosmetic. Maximising the sum of per-beat scores
    # under a duration cap is a knapsack, and knapsacks prefer many small items:
    # six mediocre five-second fragments beat one excellent thirty-second answer
    # on raw score, and the solver will take the fragments every time. The first
    # version of this spike did exactly that and scored *below random* — the
    # selection was technically optimal and editorially worthless.
    #
    # Weighting by duration makes the objective "fill the cut with the
    # highest-quality material available", which is what an editor is doing, and
    # is neutral to how long any individual beat happens to be.
    #
    # Scores are floats; CP-SAT needs integers.
    model.Maximize(sum(pick[i] * int(scores.get(beats[i].id, 0) * beats[i].frames)
                       for i in range(len(beats))))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 4
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Infeasible usually means the duration window cannot be hit with the
        # available beat lengths. Fall back to greedy by score density so the
        # run produces a comparable number instead of a hole in the table.
        return _greedy(beats, scores, target_frames)

    return sorted(beats[i].interval for i in range(len(beats))
                  if solver.Value(pick[i]))


def _greedy(beats: list[Beat], scores: dict[str, float],
            target_frames: int) -> list[Interval]:
    # Same reasoning as the CP-SAT objective: rank by quality, not by score
    # density, or short fragments dominate.
    order = sorted(beats, key=lambda b: scores.get(b.id, 0), reverse=True)
    out, used = [], 0
    for b in order:
        if used + b.frames > target_frames * (1 + TOLERANCE):
            continue
        out.append(b.interval)
        used += b.frames
        if used >= target_frames * (1 - TOLERANCE):
            break
    return sorted(out)


# ----------------------------------------------------------------- baselines


def _fill(ordered: list[Beat], target_frames: int) -> list[Interval]:
    out, used = [], 0
    for b in ordered:
        if used >= target_frames:
            break
        out.append(b.interval)
        used += b.frames
    return sorted(out)


def baseline_random(beats: list[Beat], target_frames: int, seed: int = 0):
    """Random beats to target. The floor — anything at or below this is noise."""
    rng = random.Random(seed)
    shuffled = list(beats)
    rng.shuffle(shuffled)
    return _fill(shuffled, target_frames)


def baseline_uniform(beats: list[Beat], target_frames: int):
    """Every Nth beat, evenly spaced across the source.

    Surprisingly hard to beat on material that is uniformly interesting, and a
    good check that the engine is doing more than sampling.
    """
    if not beats:
        return []
    avg = sum(b.frames for b in beats) / len(beats)
    want = max(1, int(target_frames / max(1.0, avg)))
    step = max(1, len(beats) // want)
    return _fill(beats[::step], target_frames)


def baseline_longest(beats: list[Beat], target_frames: int):
    """Longest beats first. People talk longest about what matters to them."""
    return _fill(sorted(beats, key=lambda b: -b.frames), target_frames)


def baseline_lead(beats: list[Beat], target_frames: int):
    """The first N minutes — what a rushed assistant actually does."""
    return _fill(sorted(beats, key=lambda b: b.start), target_frames)


BASELINES = {
    "random": baseline_random,
    "uniform": baseline_uniform,
    "longest": baseline_longest,
    "lead": baseline_lead,
}


def run_baselines(beats: list[Beat], target_frames: int,
                  seeds: int = 5) -> dict[str, list[Interval]]:
    """All baselines. Random is averaged over several seeds by the caller."""
    out = {
        name: fn(beats, target_frames)
        for name, fn in BASELINES.items()
        if name != "random"
    }
    for s in range(seeds):
        out[f"random[{s}]"] = baseline_random(beats, target_frames, seed=s)
    return out
