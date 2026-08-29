#!/usr/bin/env python3
"""Run pipeline stages 0-4 on a media file: ingest to beats.

    python ingest.py rushes.mov --out work/
    python ingest.py interview.wav --rate 25 --language he
    python ingest.py rushes.mov --provider replay --replay work/x.asr.json

Emits `<name>.beats.json` in the Spike B fixture format, so a real interview can
be fed straight into the selection-quality harness:

    python ../../spikes/selection-quality/spike.py work/rushes.beats.json

...once the human cut is added to it. See spikes/selection-quality/README.md —
the editor's own EDL supplies that, no annotation needed.

**Transcription needs a Whisper model.** faster-whisper downloads it from
HuggingFace on first use, so the machine running this needs access to
`huggingface.co`, or a pre-fetched model passed via --model-path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mishne.pipeline.steps import audio as audio_step  # noqa: E402
from mishne.pipeline.steps import prepare, speakers, structure, transcribe, vad  # noqa: E402
from mishne.timecode import Rate, frames_to_tc, ms_to_frames  # noqa: E402

GREEN, DIM, YELLOW, RESET = "\033[32m", "\033[2m", "\033[33m", "\033[0m"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path)
    ap.add_argument("--out", type=Path, default=Path("work"))
    ap.add_argument("--provider", default="faster-whisper",
                    choices=["faster-whisper", "replay"])
    ap.add_argument("--replay", type=Path,
                    help="stored .asr.json to replay instead of transcribing")
    ap.add_argument("--model", default="base",
                    help="whisper size: tiny, base, small, medium, large-v3")
    ap.add_argument("--model-path", help="local model directory (offline)")
    ap.add_argument("--language", default=None,
                    help="ISO code; omit to auto-detect")
    ap.add_argument("--rate", type=float, default=None,
                    help="frame rate for audio-only input, e.g. 25")
    ap.add_argument("--skip-asr", action="store_true",
                    help="stages 0, 1 and 3 only — probe, audio, VAD")
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # -- Stage 0 ------------------------------------------------------------
    assume = None
    if args.rate:
        num, den = ((24000, 1001) if abs(args.rate - 23.976) < 0.01 else
                    (30000, 1001) if abs(args.rate - 29.97) < 0.01 else
                    (int(args.rate), 1))
        assume = Rate(num, den)
    info = prepare.probe(args.media, assume_rate=assume)
    print(f"  0 probe        {GREEN}ok{RESET}  {info.codec} "
          f"{info.width}x{info.height} · {info.rate} · "
          f"start {info.start_tc} · {info.duration_s / 60:.1f} min · "
          f"{len(info.audio)} audio")

    # -- Stage 1 ------------------------------------------------------------
    tracks = audio_step.extract(info, args.out)
    if not tracks:
        print("  1 audio        no audio streams — nothing to transcribe")
        return 1
    for t in tracks:
        print(f"  1 audio        {GREEN}ok{RESET}  track {t.track_index} · "
              f"{t.integrated_lufs:.1f} LUFS · peak {t.peak_dbfs:.1f} dBFS")

    # -- Stage 3 ------------------------------------------------------------
    speech = vad.build(tracks[0].path)
    speech_ms = sum(e - s for s, e in speech.speech)
    print(f"  3 vad          {GREEN}ok{RESET}  {len(speech.speech)} speech "
          f"segments · {speech_ms / 60000:.1f} min speech "
          f"({100 * speech_ms / max(1, speech.duration_ms):.0f}% of runtime)")

    if args.skip_asr:
        print(f"\n  {YELLOW}stopped before transcription (--skip-asr){RESET}")
        return 0

    # -- Stage 2 ------------------------------------------------------------
    kwargs = ({"path": args.replay} if args.provider == "replay"
              else {"model": args.model, "model_path": args.model_path})
    try:
        result = transcribe.run(tracks[0].path, args.out,
                                provider=args.provider,
                                language=args.language, **kwargs)
    except Exception as exc:
        print(f"  2 transcribe   {YELLOW}failed{RESET}  {type(exc).__name__}: {exc}")
        print(f"\n  {YELLOW}If this is a proxy or network error, the Whisper "
              f"model could not be\n  downloaded. Allowlist huggingface.co, or "
              f"fetch the model once and pass\n  --model-path.{RESET}")
        return 1
    print(f"  2 transcribe   {GREEN}ok{RESET}  {len(result.words)} words · "
          f"{result.language} · {result.provider}/{result.model}")

    # -- Speaker attribution ------------------------------------------------
    # Runs between transcription and structuring: stage 4 splits beats on
    # speaker change, so speakers must be assigned first.
    attribution = speakers.attribute_from_files(
        result.words, {t.track_index: t.path for t in tracks})
    named = ", ".join(
        f"{s.display} ({s.speech_ms / 1000:.0f}s)" for s in attribution.speakers)
    flag = "" if attribution.reliable else f"  {YELLOW}unreliable{RESET}"
    print(f"    speakers     {GREEN}ok{RESET}  {len(attribution.speakers)} · "
          f"{named}{flag}")
    for note in attribution.notes:
        print(f"                 {DIM}{note}{RESET}")

    # -- Stage 4 ------------------------------------------------------------
    beats = structure.build(result.words, speech, language=result.language,
                            loudness_lufs=tracks[0].integrated_lufs)
    flagged = sum(1 for b in beats if b.flags)
    print(f"  4 structure    {GREEN}ok{RESET}  {len(beats)} beats · "
          f"{flagged} flagged")

    counts: dict[str, int] = {}
    for b in beats:
        for f in b.flags:
            counts[f] = counts.get(f, 0) + 1
    if counts:
        print(f"                 {DIM}"
              + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
              + RESET)

    # -- Emit ---------------------------------------------------------------
    out_path = args.out / f"{args.media.stem}.beats.json"
    out_path.write_text(json.dumps({
        "name": args.media.stem,
        "fps": info.rate.fps,
        "rate": {"num": info.rate.num, "den": info.rate.den,
                 "drop_frame": info.rate.drop_frame},
        "source_start_frames": info.start_tc_frames,
        "language": result.language,
        "asr": {"provider": result.provider, "model": result.model},
        # Speakers are unnamed until a person names them. `label` stays empty
        # and `confirmed` false — the UI shows defaultLabel and invites a rename.
        # Nothing downstream may put an unconfirmed name in a delivered artifact.
        "speakers": attribution.to_dict(),
        "notes": "Generated by apps/api/ingest.py — stages 0-4.",
        "beats": [
            {
                "id": b.id,
                # Frames from the start of the source timecode, which is what
                # every downstream artifact references.
                "start": info.start_tc_frames + ms_to_frames(b.start_ms, info.rate),
                "end": info.start_tc_frames + ms_to_frames(b.end_ms, info.rate),
                "tc_in": frames_to_tc(
                    info.start_tc_frames + ms_to_frames(b.start_ms, info.rate),
                    info.rate),
                "speaker": b.speaker,
                "text": b.text,
                "flags": b.flags,
                "confidence": round(b.mean_confidence, 3),
            }
            for b in beats
        ],
        # Filled in from the editor's own EDL — see the Spike B README.
        "human_cut": [],
    }, ensure_ascii=False, indent=1))

    print(f"\n  {GREEN}wrote{RESET} {out_path}  {DIM}({time.time() - t0:.1f}s)"
          f"{RESET}")
    print(f"  {DIM}Add the editor's cut to \"human_cut\" and run the "
          f"selection-quality spike against it.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
