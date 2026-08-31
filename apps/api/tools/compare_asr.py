"""Run one piece of audio through several transcription engines and compare.

    python tools/compare_asr.py ../../samples/SyncDaniel.aaf --language he
    python tools/compare_asr.py ../../samples/Gugu.aaf --language en \
        --engines xai/grok-stt google/gemini-3.5-transcribe \
        --baseline ../../models/faster-whisper-large-v3

## What this answers, and what it does not

It answers, for the same audio: what each engine costs, how long it takes, how
many words it returns, how much filler survives, how many speakers it finds, and
**how far apart two engines' word boundaries are** where they agree on the word.

It does not answer word error rate. That needs a reference transcript nobody has
written, and it is not the number that decides this anyway: ADR-0003 puts
timestamp boundary precision above WER, because a cut lands between words and a
transcript that is 2% more accurate with sloppy boundaries makes worse cuts.

Nor does it answer whether the cut is better — that is the A1 corpus's job.

## The three measurements that are free and worth having

**Filler retention.** Removing "um" is this product's job and it cannot do it if
the ASR already did. Both managed engines are asked for verbatim output; this
counts whether they delivered it. An engine that silently cleans up is
disqualified regardless of price, and the count is how you find out before a
customer does.

**Boundary agreement.** For words two engines both return in the same order, the
median difference in start time. Small means the choice is a cost decision.
Large means one of them is placing words somewhere the audio is not, and the
cheaper one is not cheaper.

**Cost against a measured duration.** Not an estimate — what the engine billed
for the audio it was actually given.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mishne.asr import catalog, get_provider  # noqa: E402
from mishne.config import load_env_file  # noqa: E402
from mishne.asr.base import ASRResult  # noqa: E402
from mishne.language import is_rtl_language  # noqa: E402
from mishne.pipeline import project  # noqa: E402

#: Rough, and deliberately so. Filler is language specific and this is a
#: retention check rather than a filler detector — the pipeline has its own.
FILLER = {
    "en": {"um", "uh", "erm", "ah", "hmm", "like", "you know", "i mean"},
    # Hebrew fillers. "אז" is left OUT deliberately: it is "so", and it opens
    # a sentence as often as it fills a pause — counting it would make every
    # transcript look full of filler and tell you nothing about whether the
    # engine kept any.
    "he": {"אמ", "אה", "אהה", "כאילו", "יעני", "בעצם", "אהם"},
}


#: How a local model directory is named on the engine list. A prefix rather
#: than a split on "/": a model path has slashes in it, and
#: `"local/../../models/faster-whisper-large-v3".rpartition("/")` yields the
#: provider `local/../../models`, which is not a provider. That is exactly what
#: happened on the first run that used --baseline.
LOCAL = "local/"


def transcribe(engine: str, audio: Path, language: str | None,
               work: Path) -> tuple[ASRResult, float]:
    t0 = time.time()
    if engine.startswith(LOCAL) or engine == "faster-whisper":
        model_path = engine[len(LOCAL):] if engine.startswith(LOCAL) else None
        result = get_provider("faster-whisper", model_path=model_path,
                              model="large-v3").transcribe(
            audio, language=language)
    else:
        provider_name, _, model = engine.rpartition("/")
        result = get_provider(provider_name, model=model,
                              work_dir=work / model).transcribe(
            audio, language=language)
    return result, time.time() - t0


def filler_ratio(result: ASRResult, language: str | None) -> float:
    words = [w.text.strip(".,!?").lower() for w in result.words]
    if not words:
        return 0.0
    vocab = FILLER.get((language or result.language or "en").split("-")[0], set())
    return sum(1 for w in words if w in vocab) / len(words)


def boundary_agreement(a: ASRResult, b: ASRResult) -> tuple[int, float] | None:
    """Median |start_a - start_b| in ms over words both engines returned.

    Matched by walking both word lists in order and pairing equal tokens, which
    is crude and sufficient: what is being measured is where two engines put
    the same word, not how to align two different transcripts.
    """
    i = j = 0
    diffs: list[int] = []
    while i < len(a.words) and j < len(b.words):
        wa, wb = a.words[i], b.words[j]
        if wa.text.strip(".,!?").lower() == wb.text.strip(".,!?").lower():
            diffs.append(abs(wa.start_ms - wb.start_ms))
            i += 1
            j += 1
        elif wa.start_ms <= wb.start_ms:
            i += 1
        else:
            j += 1
    if not diffs:
        return None
    return len(diffs), statistics.median(diffs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path)
    ap.add_argument("--language", default=None)
    ap.add_argument("--engines", nargs="+", default=None,
                    help="provider/id pairs; default is every catalogued "
                         "engine that speaks the language and has a key")
    ap.add_argument("--baseline", type=Path, default=None,
                    help="a local faster-whisper model directory, to compare "
                         "the managed engines against what they replaced")
    ap.add_argument("--work", type=Path, default=Path("work-asr"))
    args = ap.parse_args(argv)
    load_env_file(Path(__file__).resolve().parents[1] / ".env")

    from mishne.asr import routing

    engines = args.engines or [e.key for e in routing.plan(args.language)]
    if args.baseline:
        engines.append(f"{LOCAL}{args.baseline}")
        print("note: the self-hosted baseline runs at roughly real time on CPU. "
              "For this material that is the wait the managed engines exist to "
              "remove — leave it running.\n")
    if not engines:
        print("no engine available — set XAI_API_KEY or GEMINI_API_KEY, "
              "or pass --baseline with a local model directory")
        return 1

    # The providers log structured lines as they go, and a JSON line landing in
    # the middle of a table makes the table unreadable. They go to stderr so
    # both survive: `2>/dev/null` for the table alone, `2>&1 | ...` for both.
    import logging as stdlib_logging

    stdlib_logging.basicConfig(stream=sys.stderr, level=stdlib_logging.INFO,
                               format="%(message)s", force=True)
    import structlog

    structlog.configure(
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr)
    )

    args.work.mkdir(parents=True, exist_ok=True)
    # Stages 0 and 1 as the pipeline runs them, not a reimplementation:
    # `prepare.probe` cannot read an AAF at all — ffprobe does not know the
    # format — and the branch that flattens a sequence into audio first lives
    # in `stage_prepare`. Calling probe directly here worked on an mp4 and
    # failed on exactly the Hebrew sample this tool exists to measure.
    prepared = project.stage_prepare(args.media, args.work / "prep")
    tracks = project.stage_audio(prepared, args.work / "audio")
    wav = tracks[0].path
    info = prepared.info
    source_hours = info.duration_frames / (info.rate.fps or 25) / 3600

    if is_rtl_language(args.language):
        print("note: RTL material. Word text below is stored logically; a "
              "terminal may render it reversed. The numbers are unaffected.\n")

    print(f"{args.media.name} · {source_hours * 60:.1f} min · "
          f"{args.language or 'language unset'}")
    when = catalog.verified_on()
    print(f"prices from engines.json, verified {when}\n" if when else "")

    header = (f"{'engine':<34}{'words':>7}{'spk':>5}{'filler':>8}"
              f"{'wall':>8}{'cost':>10}{'$/src hr':>10}")
    print(header)
    print("-" * len(header))

    results: dict[str, ASRResult] = {}
    for engine in engines:
        try:
            result, wall = transcribe(engine, wav, args.language, args.work)
        except Exception as exc:  # noqa: BLE001 — a failed engine is a result
            print(f"{engine:<34}{'—':>7}   {type(exc).__name__}: {exc}")
            continue
        results[engine] = result
        speakers = len({w.speaker for w in result.words if w.speaker})
        per_hour = result.usd_per_source_hour
        cost = (f"${result.cost_usd:.4f}"
                + ("*" if result.cost_estimated else "")) if result.priced else "?"
        print(f"{engine:<34}{len(result.words):>7}{speakers:>5}"
              f"{filler_ratio(result, args.language) * 100:>7.1f}%"
              f"{wall:>7.1f}s{cost:>10}"
              f"{('—' if per_hour is None else f'${per_hour:.3f}'):>10}")

    if any(r.cost_estimated for r in results.values()):
        print("\n* estimated from published rates — the vendor reported no "
              "usage counts. Not a number to reconcile against an invoice.")

    keys = list(results)
    if len(keys) > 1:
        print("\nboundary agreement, median |Δ start| on words both returned")
        # Every pair, not each against the first. With three engines the
        # interesting fact is not how far each is from one of them but which
        # one is the outlier: two agreeing closely while a third differs from
        # both by the same amount says something no single column shows, and
        # comparing only against engines[0] hides it behind whichever engine
        # happened to be listed first.
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                agreement = boundary_agreement(results[a], results[b])
                if agreement is None:
                    print(f"  {a} vs {b}: no words in common — that is the "
                          f"finding, not a bug in the comparison")
                    continue
                matched, median = agreement
                share = matched / max(len(results[a].words), 1)
                print(f"  {a} vs {b}: {median:.0f} ms over {matched} words "
                      f"({share * 100:.0f}%)")
        print("\nA large median with high overlap means the engines agree on "
              "the words and disagree on where they are, which is the failure "
              "that costs cuts rather than accuracy (ADR-0003). One frame at "
              "25 fps is 40 ms.")

    for engine, result in results.items():
        out = args.work / f"{wav.stem}.{engine.replace('/', '_')}.asr.json"
        import json
        out.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1))
    print(f"\ntranscripts written to {args.work}/ — pass one to "
          f"`run.py --replay` to cut from it without paying again.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
