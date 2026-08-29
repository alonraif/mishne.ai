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


def solve(beats: list[Beat], scores: dict[str, float], brief,
          asset_order: dict[str, int] | None = None) -> list[Selection]:
    """Choose beats to fill the target duration with the best material.

    `asset_order` maps asset id to its position in the project, and is what
    "chronological" means once a cut draws on several uploads. Beats carry their
    own asset's local timing — there is deliberately no global timeline (see
    pipeline/project.py) — so ordering across assets is `(asset, start)`, which
    is the only honest reading of chronology for material shot on different
    days. Empty for a single-asset job, where it changes nothing.
    """
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

    # Non-overlap. Beats never overlapped, so this constraint did not exist and
    # did not need to. Candidate spans do: stage 6 offers the same long answer
    # trimmed three ways, and picking two of them would play the overlapping
    # seconds twice. Disjoint spans from one parent are fine and often wanted —
    # keep the opening and the payoff, drop the middle — so the test is on time,
    # not on parentage.
    #
    # Quadratic in the candidate count, which is why stage 6 caps spans per
    # beat. Sorting first lets the inner loop stop early instead of comparing
    # every pair in the job.
    ordered = sorted(eligible, key=lambda b: (b.asset_id, b.start_ms))
    clashes = 0
    for i, a in enumerate(ordered):
        for c in ordered[i + 1:]:
            if c.asset_id != a.asset_id or c.start_ms >= a.end_ms:
                break
            model.Add(pick[a.id] + pick[c.id] <= 1)
            clashes += 1
    solve.overlap_constraints = clashes

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
        return _greedy(eligible, scores, target_ms, tol_ms, asset_order or {})

    chosen = [by_id[bid] for bid, var in pick.items() if solver.Value(var)]
    return _order(chosen, scores, brief, asset_order or {})


def _greedy(beats: list[Beat], scores: dict[str, float],
            target_ms: int, tol_ms: int,
            asset_order: dict[str, int] | None = None) -> list[Selection]:
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
    key = _source_key(asset_order or {})
    return [Selection(b, i, scores.get(b.id, 0))
            for i, b in enumerate(sorted(chosen, key=key))]


def _source_key(asset_order: dict[str, int]):
    """Sort key for "where this came from", across one asset or many.

    A beat's `start_ms` is local to its own upload, so on its own it would
    interleave three separate interviews into nonsense. Asset position comes
    first; within an asset, nothing changes.
    """
    return lambda b: (asset_order.get(b.asset_id, 0), b.start_ms, b.end_ms)


def _order(beats: list[Beat], scores: dict[str, float], brief,
           asset_order: dict[str, int] | None = None) -> list[Selection]:
    """Put the selection into cut order according to the brief's shape."""
    shape = brief.narrative_shape
    src = _source_key(asset_order or {})

    if shape == "inverted_pyramid":
        ordered = sorted(beats, key=lambda b: -scores.get(b.id, 0))
    elif shape == "thematic":
        # No topic model yet, so group by speaker and keep source order within
        # each group. An honest approximation; real clustering is stage 6 work.
        # Grouping by speaker deliberately crosses assets: the same person in
        # two sessions is one thread, provided the speakers have been merged.
        ordered = sorted(beats, key=lambda b: (b.speaker, *src(b)))
    else:
        # chronological and q_and_a both preserve source order; q_and_a relies
        # on questions and answers already being adjacent in the source.
        ordered = sorted(beats, key=src)

    return [Selection(b, i, scores.get(b.id, 0)) for i, b in enumerate(ordered)]
