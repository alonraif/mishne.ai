"""The metric. This file is the actual deliverable of Spike B.

## What is being measured

Given the same rushes, how much of what a human editor *actually used* does the
engine also select? Everything is measured on the **time axis in frames**, not
on beats, because beat boundaries are our invention and the editor's in-points
are not. Comparing beat sets would flatter us: we would be grading our own
segmentation.

## Why recall matters more than precision

A rough cut that runs slightly long but contains every soundbite the editor
wanted saves them an afternoon. A rough cut that is exactly the right length and
misses the best line wastes their morning and loses their trust. The two errors
are not symmetric, and a metric that treats them as symmetric will drive the
wrong decisions.

So the headline number is **recall-weighted F (beta = 2)**, with precision and
recall both reported so the trade is visible rather than hidden inside one
number.

## Why baselines are not optional

"41% overlap" means nothing on its own. It could be excellent or it could be
what you get by picking at random. Every run therefore scores the same material
with several trivial selectors:

- **random** — beats picked at random to the target duration
- **uniform** — every Nth beat, evenly spaced
- **longest** — the longest beats until the target is filled
- **lead** — the first N minutes, which is what a rushed assistant does

If the engine cannot beat `longest` and `lead`, it is not doing anything worth
paying for. **Lift over the best baseline is the number that decides the
project**, not the raw score.

## The ceiling nobody mentions

Two editors given the same rushes do not produce the same cut. Human-to-human
agreement is the real ceiling, and it is well below 100%. If you can obtain two
independent cuts of one source, measure it — a 55% score against a 60% human
ceiling is a very different result from 55% against a 90% ceiling. Until that is
known, treat absolute scores as provisional and watch the lift.
"""

from __future__ import annotations

from dataclasses import dataclass

Interval = tuple[int, int]  # [start_frame, end_frame)


def normalize(intervals: list[Interval]) -> list[Interval]:
    """Sort and merge overlapping intervals into a canonical disjoint set."""
    if not intervals:
        return []
    out: list[Interval] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def total(intervals: list[Interval]) -> int:
    return sum(e - s for s, e in normalize(intervals))


def intersect(a: list[Interval], b: list[Interval]) -> list[Interval]:
    a, b = normalize(a), normalize(b)
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def union(a: list[Interval], b: list[Interval]) -> list[Interval]:
    return normalize(a + b)


@dataclass
class Score:
    """One selector's result against the human cut."""

    name: str
    recall: float          # of the human's selection, how much did we find
    precision: float       # of what we picked, how much did they use
    f2: float              # recall-weighted F — the headline
    iou: float
    selected_frames: int
    human_frames: int
    overlap_frames: int
    duration_error: float  # signed, relative to target

    @property
    def verdict(self) -> str:
        if self.f2 >= 0.60:
            return "strong"
        if self.f2 >= 0.40:
            return "viable"
        return "weak"


def score(name: str, selected: list[Interval], human: list[Interval],
          target_frames: int) -> Score:
    sel, hum = normalize(selected), normalize(human)
    sel_n, hum_n = total(sel), total(hum)
    ov = total(intersect(sel, hum))

    recall = ov / hum_n if hum_n else 0.0
    precision = ov / sel_n if sel_n else 0.0

    # F-beta with beta=2: recall weighted four times precision.
    beta2 = 4.0
    denom = (beta2 * precision) + recall
    f2 = ((1 + beta2) * precision * recall / denom) if denom else 0.0

    u = total(union(sel, hum))
    return Score(
        name=name,
        recall=recall,
        precision=precision,
        f2=f2,
        iou=ov / u if u else 0.0,
        selected_frames=sel_n,
        human_frames=hum_n,
        overlap_frames=ov,
        duration_error=(sel_n - target_frames) / target_frames if target_frames else 0.0,
    )


def lift(engine: Score, baselines: list[Score]) -> tuple[float, str]:
    """Engine F2 relative to the strongest baseline.

    Returns (ratio, name of the baseline beaten). A ratio at or below 1.0 means
    the engine is not earning its cost.
    """
    if not baselines:
        return (float("inf"), "none")
    best = max(baselines, key=lambda b: b.f2)
    if best.f2 <= 0:
        return (float("inf"), best.name)
    return (engine.f2 / best.f2, best.name)
