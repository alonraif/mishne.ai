"""Stage 7 — selection.

**Deterministic.** The LLM scores; a solver selects. Language models cannot hit
a duration target — ask for exactly ten minutes and you get seven or fourteen —
and duration is the one constraint the customer stated explicitly.
See docs/adr/0004-constraint-solver-for-selection.md.

The objective is **quality-weighted screen time**, not the sum of scores.
Maximising raw score under a duration cap is a knapsack, and knapsacks prefer
many small items: six mediocre five-second fragments beat one excellent
thirty-second answer. Spike B hit exactly this and scored below random while
being provably optimal. Weighting by duration makes the objective "fill the cut
with the best material available", which is what an editor is doing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .structure import Beat


@dataclass
class Selection:
    beat: Beat
    order_idx: int
    score: float


def solve(beats: list[Beat], scores: dict[str, float], brief) -> list[Selection]:
    """Choose beats to fill the target duration with the best material."""
    from ortools.sat.python import cp_model

    eligible = [b for b in beats if scores.get(b.id, 0) > 0]
    if not eligible:
        return []

    target_ms = brief.target_duration_s * 1000
    tol_ms = brief.duration_tolerance_s * 1000

    model = cp_model.CpModel()
    pick = {b.id: model.NewBoolVar(b.id) for b in eligible}

    duration = sum(pick[b.id] * b.duration_ms for b in eligible)
    model.Add(duration >= max(0, target_ms - tol_ms))
    model.Add(duration <= target_ms + tol_ms)

    by_id = {b.id: b for b in eligible}

    # Dependency closure: a payoff without its setup reads as a non-sequitur.
    for b in eligible:
        for dep in getattr(b, "depends_on", []) or []:
            if dep in pick:
                model.Add(pick[b.id] <= pick[dep])

    # Speaker balance: when the brief names priority speakers, at least half the
    # selected time must come from them. Stated as a preference in the notes,
    # enforced as a constraint here — that is the point of having a solver.
    if brief.speaker_priority:
        priority_ids = {s for s in brief.speaker_priority}
        prio_ms = sum(pick[b.id] * b.duration_ms
                      for b in eligible if b.speaker in priority_ids)
        if any(b.speaker in priority_ids for b in eligible):
            model.Add(prio_ms * 2 >= duration)

    model.Maximize(sum(int(scores[b.id] * b.duration_ms) * pick[b.id]
                       for b in eligible))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 30.0
    solver.parameters.num_search_workers = 8
    status = solver.Solve(model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return _greedy(eligible, scores, target_ms, tol_ms)

    chosen = [by_id[bid] for bid, var in pick.items() if solver.Value(var)]
    return _order(chosen, scores, brief)


def _greedy(beats: list[Beat], scores: dict[str, float],
            target_ms: int, tol_ms: int) -> list[Selection]:
    """Fallback when the duration window cannot be hit exactly.

    Usually means the available beat lengths cannot sum into the window — a very
    short target against long answers. Better to deliver a near-miss the editor
    can trim than to fail the job.
    """
    order = sorted(beats, key=lambda b: -scores.get(b.id, 0))
    chosen, used = [], 0
    for b in order:
        if used + b.duration_ms > target_ms + tol_ms:
            continue
        chosen.append(b)
        used += b.duration_ms
        if used >= target_ms - tol_ms:
            break
    return [Selection(b, i, scores.get(b.id, 0))
            for i, b in enumerate(sorted(chosen, key=lambda x: x.start_ms))]


def _order(beats: list[Beat], scores: dict[str, float], brief) -> list[Selection]:
    """Put the selection into cut order according to the brief's shape."""
    shape = brief.narrative_shape

    if shape == "inverted_pyramid":
        ordered = sorted(beats, key=lambda b: -scores.get(b.id, 0))
    elif shape == "thematic":
        # No topic model yet, so group by speaker and keep source order within
        # each group. An honest approximation; real clustering is stage 6 work.
        ordered = sorted(beats, key=lambda b: (b.speaker, b.start_ms))
    else:
        # chronological and q_and_a both preserve source order; q_and_a relies
        # on questions and answers already being adjacent in the source.
        ordered = sorted(beats, key=lambda b: b.start_ms)

    return [Selection(b, i, scores.get(b.id, 0)) for i, b in enumerate(ordered)]
