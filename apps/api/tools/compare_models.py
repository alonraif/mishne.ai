"""Run one piece of material through several scoring models and compare.

    python tools/compare_models.py ../../samples/SyncDaniel.aaf \
        --language he --model-path ../../models/faster-whisper-large-v3 \
        --target 40s --models anthropic/claude-opus-5 anthropic/claude-sonnet-5 \
                              google/gemini-3.7-flash

## What this answers, and what it does not

It answers: what does each model cost on this material, how long does it take,
does it obey the hard constraints, and **do the models actually disagree about
the cut**.

It does not answer which cut is better. That is taste, it needs an editor's own
EDL to compare against, and it is what the A1 corpus is for (ADR-0011). Nothing
here is evidence that a cheap model is good enough — it is evidence about how
much is at stake in finding out.

The agreement number is the useful one. If a model six times cheaper selects the
same spans, the premium is buying nothing and the quality question is moot for
this material. If it selects different spans, you have quantified the decision
rather than argued about it, and you know what the corpus is worth.

## Why it shells out to run.py

The pipeline is what is under test, not a reimplementation of it. Each run is a
real one, and the content-addressed ingest cache means transcription happens on
the first run only (ADR-0016) — so comparing five models costs five scoring
passes, not five transcriptions.

## The estimate, checked against what was charged

C1's definition of done asks for the estimate to be within a documented margin
of what is actually charged. Each row carries `Router.estimate` for the model
that ran alongside what it billed, because a total that happens to be right
while its parts are wrong is not a calibrated estimator — it is two errors
cancelling, and they stop cancelling as soon as either one moves.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = HERE.parent
sys.path.insert(0, str(API / "src"))


def _manifest(out_dir: Path) -> dict | None:
    hits = sorted(out_dir.glob("*.mishne.json"))
    return json.loads(hits[0].read_text(encoding="utf-8")) if hits else None


def _spans(manifest: dict) -> set[tuple[str, str]]:
    """The cut, as a set of (in, out) timecodes.

    Timecodes rather than beat ids: two models can select different beats that
    resolve to the same picture, and what the customer receives is the frames.
    """
    return {(c["tcIn"], c["tcOut"]) for c in manifest.get("cuts", [])}


def _agreement(a: set, b: set) -> float:
    """Jaccard overlap of two cuts. 1.0 is the same cut."""
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 0.0


def _estimate_for(model: str, calls: dict[str, int]) -> float:
    """What the router would have quoted, using the model that actually ran."""
    from mishne.llm.router import Router

    pins = {}
    for task in calls:
        pins[f"MISHNE_MODEL_{task.upper()}"] = model
    previous = {k: os.environ.get(k) for k in pins}
    os.environ.update(pins)
    try:
        return Router().estimate(calls)["usd"]
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def run_one(media: str, model: str, out_root: Path, extra: list[str]) -> dict:
    """One full pipeline run with `score` pinned to `model`."""
    slug = model.replace("/", "_")
    out = out_root / slug
    # UPPERCASE: `_pinned_for` reads MISHNE_MODEL_{TASK.upper()}. Spelled
    # lowercase this pins nothing, silently, and every row of the comparison is
    # the same model with a different label on it.
    env = {**os.environ, "MISHNE_MODEL_SCORE": model}

    started = time.time()
    proc = subprocess.run(
        [sys.executable, str(API / "run.py"), media, "--out", str(out), *extra],
        cwd=API, env=env, capture_output=True, text=True,
    )
    wall = time.time() - started

    manifest = _manifest(out)
    if proc.returncode != 0 or manifest is None:
        return {"model": model, "ok": False,
                # The last line only: a traceback here is noise, and the
                # interesting failures (a 400, a refusal) say so on one line.
                "error": (proc.stdout or proc.stderr).strip().splitlines()[-1:][0]
                if (proc.stdout or proc.stderr).strip() else "no output",
                "wall_s": wall}

    calls = manifest.get("llmCalls", [])
    per_task: dict[str, int] = {}
    for call in calls:
        per_task[call["task"]] = per_task.get(call["task"], 0) + 1

    return {
        "model": model,
        "ok": True,
        "actual_usd": manifest.get("llmCostUsd", 0.0),
        "estimate_usd": _estimate_for(model, per_task) if per_task else 0.0,
        "calls": per_task,
        # A model that could not produce parseable JSON, or that proposed spans
        # the silence gate refused, has told you something measurable about
        # whether it can hold a constraint — no corpus required.
        "unparsed": sum(1 for c in calls if not c.get("ok", True)),
        "violations": sum(c.get("violations", 0) for c in calls),
        "proposals": sum(c.get("proposals", 0) for c in calls),
        "model_ms": sum(c.get("latency_ms", 0) for c in calls),
        "wall_s": wall,
        "spans": _spans(manifest),
        "cut_count": len(manifest.get("cuts", [])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compare_models")
    parser.add_argument("media")
    parser.add_argument("--models", nargs="+", required=True,
                        help="provider/model ids to pin `score` to, reference first")
    parser.add_argument("--out", default="")
    args, extra = parser.parse_known_args(argv)

    out_root = Path(args.out) if args.out else API.parent.parent / "build" / "model-compare"
    out_root.mkdir(parents=True, exist_ok=True)

    results = []
    for model in args.models:
        print(f"  running {model} ...", flush=True)
        results.append(run_one(args.media, model, out_root, extra))

    ok = [r for r in results if r["ok"]]
    print()
    print(f"{'model':34} {'cost':>9} {'est':>9} {'model s':>8} {'cuts':>5} "
          f"{'agree':>6}  notes")
    reference = ok[0]["spans"] if ok else set()
    for r in results:
        if not r["ok"]:
            print(f"{r['model']:34} {'FAILED':>9}  {r['error'][:60]}")
            continue
        agree = _agreement(reference, r["spans"])
        notes = []
        if r["unparsed"]:
            notes.append(f"{r['unparsed']} unparsed")
        if r["proposals"]:
            notes.append(f"{r['violations']}/{r['proposals']} refused by the gate")
        drift = (
            f"{(r['actual_usd'] / r['estimate_usd'] - 1) * 100:+.0f}%"
            if r["estimate_usd"] else "—"
        )
        print(f"{r['model']:34} ${r['actual_usd']:>8.4f} ${r['estimate_usd']:>8.4f} "
              f"{r['model_ms'] / 1000:>8.1f} {r['cut_count']:>5} {agree:>6.2f}"
              f"  est {drift}" + ("  · " + ", ".join(notes) if notes else ""))

    if len(ok) > 1:
        cheapest = min(ok, key=lambda r: r["actual_usd"])
        saving = ok[0]["actual_usd"] - cheapest["actual_usd"]
        print()
        print(f"Reference is {ok[0]['model']}. `agree` is the Jaccard overlap of the")
        print("selected spans with it — 1.00 means the same cut, frame for frame.")
        if saving > 0:
            print(f"Cheapest run saves ${saving:.4f} on this material "
                  f"({saving / ok[0]['actual_usd'] * 100:.0f}%) at "
                  f"{_agreement(reference, cheapest['spans']):.2f} agreement.")
        print()
        print("Agreement is not quality. A cheaper model that picks the same spans")
        print("is buying you nothing to give up; one that picks different spans has")
        print("not been shown to be worse — only different, and which is better is")
        print("what the A1 corpus exists to answer (ADR-0011).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
