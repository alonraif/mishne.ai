#!/usr/bin/env python3
"""Spike B — selection quality.

Answers one question: **does the engine select what a human editor actually
used?**

Spike A asked whether we can deliver a file. This asks whether the file is worth
delivering. It is the one that decides whether the product is worth building.

    python spike.py fixtures/harbour.json
    python spike.py fixtures/harbour.json --scorer anthropic
    python spike.py --pair rushes.wav --cut finished.edl --fps 25

See README.md for how to read the numbers, and why the baselines matter more
than the headline score.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import diagnose
import metrics
import scorers
import selection as selector
from corpus import Pair, load_fixture

GREEN, RED, YELLOW, DIM, BOLD, RESET = (
    "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
)


def bar(value: float, width: int = 22) -> str:
    filled = int(round(max(0.0, min(1.0, value)) * width))
    return "█" * filled + "·" * (width - filled)


def run(pair: Pair, scorer_name: str, show_diagnostics: bool = False) -> int:
    fps = pair.fps
    target = pair.human_frames
    beat_frames = sum(b.frames for b in pair.beats)
    base_rate = target / beat_frames if beat_frames else 0

    print(f"\n{'=' * 72}\n Spike B — selection quality\n{'=' * 72}")
    print(f" material   {pair.name}")
    print(f" beats      {len(pair.beats)}  ({beat_frames / fps / 60:.1f} min of speech)")
    print(f" human cut  {target / fps / 60:.2f} min, {len(pair.human_beats())} beats")
    print(f" base rate  {base_rate:.1%} {DIM}— a random pick scores about this{RESET}")
    print(f" scorer     {scorer_name}\n")

    brief = {
        "target_duration_s": round(target / fps),
        "tone": ["conversational"],
        "narrative_shape": "chronological",
    }

    scorer = scorers.get_scorer(scorer_name)
    scores = scorer.score(pair.beats, brief)

    if show_diagnostics:
        diagnose.report(pair, scores)

    engine_sel = selector.solve(pair.beats, scores, target)
    engine = metrics.score(f"engine ({scorer_name})", engine_sel, pair.human, target)

    baseline_runs = selector.run_baselines(pair.beats, target)
    baselines: list[metrics.Score] = []
    randoms = []
    for name, sel in sorted(baseline_runs.items()):
        s = metrics.score(name, sel, pair.human, target)
        (randoms if name.startswith("random[") else baselines).append(s)

    if randoms:
        baselines.append(metrics.Score(
            name="random (mean of 5)",
            recall=statistics.mean(r.recall for r in randoms),
            precision=statistics.mean(r.precision for r in randoms),
            f2=statistics.mean(r.f2 for r in randoms),
            iou=statistics.mean(r.iou for r in randoms),
            selected_frames=round(statistics.mean(r.selected_frames for r in randoms)),
            human_frames=target,
            overlap_frames=round(statistics.mean(r.overlap_frames for r in randoms)),
            duration_error=statistics.mean(r.duration_error for r in randoms),
        ))

    print(f" {BOLD}{'selector':22} {'recall':>7} {'prec':>7} {'F2':>7}   "
          f"{'':22}{RESET}")
    for s in sorted(baselines + [engine], key=lambda x: -x.f2):
        is_engine = s.name.startswith("engine")
        colour = BOLD if is_engine else DIM
        print(f" {colour}{s.name:22} {s.recall:>6.1%} {s.precision:>6.1%} "
              f"{s.f2:>6.1%}{RESET}   {bar(s.f2)}")

    ratio, beaten = metrics.lift(engine, baselines)
    print()
    if ratio == float("inf"):
        verdict, colour = "no baseline scored above zero", YELLOW
    elif ratio >= 1.5:
        verdict, colour = f"{ratio:.2f}x the best baseline ({beaten})", GREEN
    elif ratio >= 1.15:
        verdict, colour = f"{ratio:.2f}x the best baseline ({beaten}) — thin", YELLOW
    else:
        verdict, colour = (
            f"{ratio:.2f}x the best baseline ({beaten}) — not earning its cost", RED)
    print(f" lift       {colour}{verdict}{RESET}")
    print(f" duration   {engine.duration_error:+.1%} against target "
          f"{DIM}(solver constraint, should be near zero){RESET}")

    print(f"\n{'=' * 72}")
    print(f" Interpretation for F2 = {engine.f2:.1%}: {BOLD}{engine.verdict}{RESET}")
    print(f" {DIM}>60% strong · 40-60% viable · <40% needs rework{RESET}")
    print(f"\n {YELLOW}These thresholds are provisional until human-to-human"
          f" agreement{RESET}")
    print(f" {YELLOW}is measured. Two editors do not agree either — see"
          f" README.{RESET}")
    print(f"{'=' * 72}\n")

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fixture", nargs="?", type=Path,
                    help="fixture JSON (a hand-built pair)")
    ap.add_argument("--scorer", default="heuristic",
                    choices=["heuristic", "anthropic"])
    ap.add_argument("--diagnose", action="store_true",
                    help="show scorer separation (AUC) — check this first when "
                         "a run scores badly")
    args = ap.parse_args()

    if not args.fixture:
        ap.error("give a fixture path, e.g. fixtures/harbour.json")

    return run(load_fixture(args.fixture), args.scorer, args.diagnose)


if __name__ == "__main__":
    sys.exit(main())
