#!/usr/bin/env python3
"""mishne.ai — raw footage to an editable rough cut, in one command.

    python run.py rushes.mov --notes "Ten minutes, tight. Lead on the closure."
    python run.py interview.mov --target 6m --language he --model large-v3
    python run.py rushes.mov --replay work/rushes_a1.asr.json   # no model needed

Produces, in --out:

    <name>.aaf         Avid Media Composer
    <name>.fcpxml      Premiere Pro, DaVinci Resolve, Final Cut Pro
    <name>.edl         universal fallback
    <name>.otio        canonical timeline
    <name>.transcript.html   what was used and why — hand this over too

This is the concierge MVP: run it on an editor's footage, hand back the folder.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mishne.language import is_rtl_language, warn_model_for_language  # noqa: E402
from mishne.pipeline.steps import (  # noqa: E402
    assemble, audio as audio_step, brief as brief_step, emit, prepare, refine,
    score as score_step, select, speakers as speakers_step, structure,
    transcript_page, transcribe, validate, vad,
)
from mishne.timecode import Rate, frames_to_tc  # noqa: E402

G, Y, R, D, B, X = ("\033[32m", "\033[33m", "\033[31m", "\033[2m",
                    "\033[1m", "\033[0m")


def parse_target(text: str | None) -> int | None:
    if not text:
        return None
    return brief_step.parse_duration(text)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--notes", default="", help="production notes, free text")
    ap.add_argument("--target", help="target length, e.g. 10m, 90s, 1:30")
    ap.add_argument("--language", default=None, help="ISO code, e.g. en, he")
    ap.add_argument("--model", default="base", help="whisper size")
    ap.add_argument("--model-path", help="local model directory (offline)")
    ap.add_argument("--replay", type=Path, help="stored .asr.json to reuse")
    ap.add_argument("--scorer", default="auto",
                    choices=["auto", "heuristic", "claude"])
    ap.add_argument("--rate", type=float, help="frame rate for audio-only input")
    ap.add_argument("--handles", type=int, default=6, help="handle frames")
    args = ap.parse_args()

    out = args.out or args.media.parent / f"{args.media.stem}_roughcut"
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    t0 = time.time()

    print(f"\n{'=' * 68}\n mishne.ai — {args.media.name}\n{'=' * 68}")

    if (msg := warn_model_for_language(args.model, args.language)):
        print(f" {Y}{msg}{X}\n")

    # 0 -------------------------------------------------------------------
    assume = None
    if args.rate:
        num, den = ((24000, 1001) if abs(args.rate - 23.976) < .01 else
                    (30000, 1001) if abs(args.rate - 29.97) < .01 else
                    (int(args.rate), 1))
        assume = Rate(num, den)
    info = prepare.probe(args.media, assume_rate=assume)
    print(f"  0 probe        {info.codec} · {info.rate} · start {info.start_tc}"
          f" · {info.duration_s / 60:.1f} min · {len(info.audio)} audio")

    # 1 -------------------------------------------------------------------
    tracks = audio_step.extract(info, work)
    if not tracks:
        print(f"  {R}no audio streams — nothing to cut{X}")
        return 1
    print(f"  1 audio        {len(tracks)} track(s) extracted")

    # 3 -------------------------------------------------------------------
    speech = vad.build(tracks[0].path)
    sp_ms = sum(e - s for s, e in speech.speech)
    print(f"  3 vad          {len(speech.speech)} segments · "
          f"{sp_ms / 60000:.1f} min speech")

    # 2 -------------------------------------------------------------------
    if args.replay:
        kwargs, provider = {"path": args.replay}, "replay"
    else:
        kwargs = {"model": args.model, "model_path": args.model_path}
        provider = "faster-whisper"
    try:
        asr = transcribe.run(tracks[0].path, work, provider=provider,
                             language=args.language, **kwargs)
    except Exception as exc:
        print(f"  2 transcribe   {R}{type(exc).__name__}: {exc}{X}")
        print(f"\n  {Y}If this is a network error the Whisper model could not "
              f"be downloaded.\n  Allowlist huggingface.co, or fetch it once "
              f"and pass --model-path.{X}")
        return 1
    lang = asr.language
    print(f"  2 transcribe   {len(asr.words)} words · {lang} · {asr.model}")
    if is_rtl_language(lang):
        print(f"                 {D}right-to-left language — transcript page "
              f"renders RTL{X}")

    # speakers -------------------------------------------------------------
    attribution = speakers_step.attribute_from_files(
        asr.words, {t.track_index: t.path for t in tracks})
    names = {s.id: s.display for s in attribution.speakers}
    print(f"    speakers     {len(attribution.speakers)} · "
          f"{', '.join(names.values())}"
          + ("" if attribution.reliable else f"  {Y}unreliable{X}"))

    # 4 -------------------------------------------------------------------
    beats = structure.build(asr.words, speech, language=lang,
                            loudness_lufs=tracks[0].integrated_lufs)
    print(f"  4 structure    {len(beats)} beats · "
          f"{sum(1 for b in beats if b.flags)} flagged")

    # 5 -------------------------------------------------------------------
    ed = brief_step.compile_brief(
        args.notes, parse_target(args.target),
        use_llm=(args.scorer != "heuristic"), language=lang,
        handle_frames=args.handles)
    print(f"  5 brief        target {ed.target_duration_s}s ±"
          f"{ed.duration_tolerance_s}s · {ed.narrative_shape}")
    for c in ed.clarifications:
        print(f"                 {D}{c}{X}")

    # 6 -------------------------------------------------------------------
    scorer = score_step.get_scorer(args.scorer)
    scores = scorer.score(beats, ed)
    scores = score_step.apply_disqualifiers(beats, scores, ed.keep_filler)
    live = sum(1 for v in scores.values() if v > 0)
    print(f"  6 score        {scorer.name} · {live} of {len(beats)} eligible")
    if scorer.name == "heuristic":
        print(f"                 {Y}control scorer — proves the plumbing, not "
              f"the cut. Do not show this to an editor as the product.{X}")

    # 7 -------------------------------------------------------------------
    picks = select.solve(beats, scores, ed)
    if not picks:
        print(f"  7 select       {R}nothing selected — target may be "
              f"unreachable with the available material{X}")
        return 1
    picked_s = sum(p.beat.duration_ms for p in picks) / 1000
    print(f"  7 select       {len(picks)} beats · {picked_s:.0f}s "
          f"({picked_s - ed.target_duration_s:+.0f}s vs target)")

    # 9 -------------------------------------------------------------------
    cuts = refine.refine(picks, speech, info.rate, info.start_tc_frames,
                         info.duration_frames, handle_frames=ed.handle_frames)
    warned = sum(1 for c in cuts if c.warnings)
    print(f"  9 refine       {len(cuts)} clips"
          + (f" · {warned} with notes" if warned else ""))

    # 10 ------------------------------------------------------------------
    timeline = assemble.build(cuts, args.media, info.rate,
                              info.start_tc_frames, info.duration_frames,
                              audio_tracks=len(tracks),
                              name=f"{args.media.stem}_roughcut")
    total = sum(c.frames for c in cuts)
    print(f" 10 assemble     {total} frames · {total / info.rate.fps:.1f}s · "
          f"record {frames_to_tc(round(timeline.global_start_time.value), info.rate)}")

    # 11 ------------------------------------------------------------------
    artifacts = emit.emit(timeline, out, args.media.stem)
    for a in artifacts:
        if a.ok:
            print(f" 11 emit         {G}{a.fmt:7}{X} {a.bytes:>9,} B  "
                  f"{D}{a.target_nle}{X}")
        else:
            print(f" 11 emit         {R}{a.fmt:7} {a.error}{X}")

    # 12 ------------------------------------------------------------------
    checks = validate.validate(timeline, artifacts, info.rate)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        mark = f"{G}pass{X}" if c.ok else f"{R}FAIL{X}"
        print(f" 12 validate     {mark}  {c.fmt}")
        if not c.ok:
            for chk in c.checks:
                if not chk.ok:
                    print(f"                   {R}{chk.name}: {chk.detail}{X}")
            if c.error:
                print(f"                   {R}{c.error}{X}")

    # transcript ----------------------------------------------------------
    page = transcript_page.render(
        beats, cuts, ed, info.rate, info.start_tc_frames,
        info.duration_frames, names, args.media.name, lang,
        out / f"{args.media.stem}.transcript.html")

    (out / f"{args.media.stem}.mishne.json").write_text(json.dumps({
        "media": args.media.name,
        "rate": {"num": info.rate.num, "den": info.rate.den,
                 "dropFrame": info.rate.drop_frame},
        "language": lang,
        "brief": ed.to_dict(),
        "scorer": scorer.name,
        "speakers": attribution.to_dict(),
        "cuts": [{"beatId": c.beat_id, "order": c.order_idx,
                  "tcIn": frames_to_tc(c.src_in, info.rate),
                  "tcOut": frames_to_tc(c.src_out, info.rate),
                  "frames": c.frames, "speaker": names.get(c.speaker, c.speaker),
                  "score": round(c.score, 1), "rationale": c.rationale,
                  "warnings": c.warnings, "text": c.text} for c in cuts],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{'=' * 68}")
    if failed:
        print(f" {R}{len(failed)} artifact(s) failed validation and should not "
              f"be delivered.{X}")
    else:
        print(f" {G}All artifacts validated.{X}")
    print(f" {B}{out}{X}  {D}({time.time() - t0:.1f}s){X}")
    print(f" {D}Hand over the whole folder — the transcript page is what "
          f"earns trust.{X}")
    print(f"{'=' * 68}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
