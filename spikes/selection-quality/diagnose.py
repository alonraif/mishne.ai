"""Scorer diagnostics.

When a selection scores badly there are two possible causes and they need
different fixes:

1. **The scorer cannot tell the classes apart.** No solver can recover from
   this; the selection is optimal with respect to scores that mean nothing.
2. **The scorer separates them but the solver picks wrongly.** That is an
   objective or constraint problem.

Printing overall recall does not distinguish them. Separation does. AUC is the
right summary: the probability that a randomly chosen beat the editor used
scores above a randomly chosen beat they did not.

    AUC 0.5   the scorer is noise
    AUC 0.7   useful signal
    AUC 0.85+ strong

Check this before touching the solver.
"""

from __future__ import annotations

from corpus import Pair


def auc(pair: Pair, scores: dict[str, float]) -> tuple[float, float, float]:
    """Returns (AUC, mean score of used beats, mean score of unused)."""
    used_ids = pair.human_beats()
    used = [scores.get(b.id, 0.0) for b in pair.beats if b.id in used_ids]
    unused = [scores.get(b.id, 0.0) for b in pair.beats if b.id not in used_ids]
    if not used or not unused:
        return (0.5, 0.0, 0.0)

    wins = sum((u > v) + 0.5 * (u == v) for u in used for v in unused)
    return (wins / (len(used) * len(unused)),
            sum(used) / len(used), sum(unused) / len(unused))


def report(pair: Pair, scores: dict[str, float], top: int = 12) -> None:
    a, mu, mv = auc(pair, scores)
    used_ids = pair.human_beats()

    verdict = ("noise" if a < 0.6 else
               "weak" if a < 0.7 else
               "useful" if a < 0.85 else "strong")
    print(f"\n scorer separation")
    print(f"   AUC              {a:.3f}  ({verdict})")
    print(f"   mean used        {mu:.1f}")
    print(f"   mean not used    {mv:.1f}")
    print(f"   gap              {mu - mv:+.1f}")

    ranked = sorted(pair.beats, key=lambda b: -scores.get(b.id, 0))
    print(f"\n top {top} by score        {'used?':>6}")
    for b in ranked[:top]:
        mark = "  yes" if b.id in used_ids else "   no"
        print(f"   {scores.get(b.id, 0):5.1f}  {mark}   "
              f"{b.text[:58]}{'…' if len(b.text) > 58 else ''}")

    missed = [b for b in ranked if b.id in used_ids][-4:]
    if missed:
        print(f"\n lowest-scored beats the editor DID use")
        for b in missed:
            print(f"   {scores.get(b.id, 0):5.1f}        "
                  f"{b.text[:58]}{'…' if len(b.text) > 58 else ''}")
