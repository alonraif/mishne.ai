#!/usr/bin/env python3
"""mishne.ai — raw footage to an editable rough cut, in one command.

    python run.py rushes.mov --notes "Ten minutes, tight. Lead on the closure."
    python run.py day1.mov day2.mov day3.mov --target 10m
    python run.py interview.mov --target 6m --language he
    python run.py interview.mov --asr faster-whisper --model large-v3  # offline
    python run.py rushes.mov --replay work/rushes_a1.asr.json   # no model needed

Several media arguments are one job drawing on several uploads, which is how
media projects actually arrive — footage over weeks, one finished piece. Each
upload is transcribed once and cached; adding a fourth reel next month re-uses
the three already done.

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

from mishne.asr import catalog as asr_catalog, routing as asr_routing  # noqa: E402
from mishne.asr.base import DEFAULT_PROVIDER  # noqa: E402
from mishne.language import is_rtl_language, warn_model_for_language  # noqa: E402
from mishne.llm import Router  # noqa: E402
from mishne.llm import catalog as llm_catalog, providers as llm_providers  # noqa: E402
from mishne.pipeline import project  # noqa: E402
from mishne.pipeline.steps import (  # noqa: E402
    assemble, brief as brief_step, emit, propose, refine,
    score as score_step, select, transcript_page, validate,
)
from mishne.timecode import Rate, frames_to_tc  # noqa: E402

G, Y, R, D, B, X = ("\033[32m", "\033[33m", "\033[31m", "\033[2m",
                    "\033[1m", "\033[0m")


def parse_target(text: str | None) -> int | None:
    return brief_step.parse_duration(text) if text else None


def assume_rate(value: float | None) -> Rate | None:
    if not value:
        return None
    num, den = ((24000, 1001) if abs(value - 23.976) < .01 else
                (30000, 1001) if abs(value - 29.97) < .01 else
                (int(value), 1))
    return Rate(num, den)


def _asr_line(provider: str, language: str | None) -> str:
    """What is about to transcribe, before an hour of audio is spent on it.

    The same courtesy the LLM line already pays: which vendor, at what price,
    and — because this is the decision most likely to be silently wrong — which
    language it was chosen for.
    """
    if provider == "faster-whisper":
        return "faster-whisper · self-hosted · roughly real time on CPU"
    engines = asr_routing.plan(language)
    if not engines:
        return f"{R}none available for {language or 'unidentified audio'}{X}"
    engine = engines[0]
    rate = engine.cost_for(3600.0)
    price = ("price unknown" if rate.usd is None
             else f"${rate.usd:.2f}/source hour"
                  + (" (estimated)" if rate.estimated else ""))
    when = asr_catalog.verified_on()
    return (f"{engine.key} · {language or 'language unset'} · {price}"
            + (f" · rates verified {when}" if when else ""))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("media", type=Path, nargs="+",
                    help="one or more uploads — all of them feed one cut")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--notes", default="", help="production notes, free text")
    ap.add_argument("--target", help="target length, e.g. 10m, 90s, 1:30")
    ap.add_argument("--language", default=None, help="ISO code, e.g. en, he")
    ap.add_argument("--asr", default=DEFAULT_PROVIDER,
                    choices=["auto", "xai", "gemini", "faster-whisper"],
                    help="'auto' routes by language across the managed "
                         "engines; 'faster-whisper' self-hosts (default: auto)")
    ap.add_argument("--model", default="base",
                    help="whisper size, with --asr faster-whisper")
    ap.add_argument("--model-path", help="local model directory (offline)")
    ap.add_argument("--replay", type=Path, help="stored .asr.json to reuse")
    ap.add_argument("--scorer", default="auto",
                    choices=["auto", "heuristic", "model", "claude"],
                    help="'claude' is kept as an alias for 'model'")
    ap.add_argument("--policy", default="balanced",
                    choices=["quality", "balanced", "cost"],
                    help="how to choose a model when several keys are set")
    ap.add_argument("--whole-cut", action="store_true",
                    help="one model call reads the whole transcript and makes "
                         "the cut (replaces stages 6 and 7)")
    ap.add_argument("--spans", default="auto",
                    choices=["auto", "model", "claude", "enumerate", "none"],
                    help="propose cuts inside long beats (default: auto)")
    ap.add_argument("--rate", type=float, help="frame rate for audio-only input")
    ap.add_argument("--handles", type=int, default=0,
                    help="extra frames each side; 0 keeps the cut frame accurate")
    ap.add_argument("--diarize", type=Path, metavar="DIR",
                    help="model dir for single-track voice separation")
    ap.add_argument("--merge-speakers", action="append", default=[],
                    metavar="A:SPK1=B:SPK2",
                    help="two voices in different uploads are one person")
    args = ap.parse_args()

    first = args.media[0]
    out = args.out or first.parent / f"{first.stem}_roughcut"
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    t0 = time.time()

    title = (first.name if len(args.media) == 1
             else f"{first.name} + {len(args.media) - 1} more")
    print(f"\n{'=' * 68}\n mishne.ai — {title}\n{'=' * 68}")

    # Report the model actually being used. Warning about the unused --model
    # default while --model-path points at large-v3 is worse than saying
    # nothing: it tells you to fix something you already fixed.
    effective_model = args.model_path or args.model
    if args.asr == "faster-whisper" and not args.replay:
        if (msg := warn_model_for_language(effective_model, args.language)):
            print(f" {Y}{msg}{X}\n")

    if args.replay and len(args.media) > 1:
        print(f" {R}--replay holds one stored transcript and cannot serve "
              f"several uploads.{X}")
        return 1

    router = Router(policy=args.policy)
    if not args.replay:
        print(f" {D}asr  {_asr_line(args.asr, args.language)}{X}")
    keys = llm_providers.available()
    if keys:
        picks = []
        for task in ("brief", "spans", "score"):
            plan = router.plan(task)
            picks.append(f"{task}→{plan[0].provider}/{plan[0].id}"
                         if plan else f"{task}→none")
        print(f" {D}llm  {'+'.join(keys)} · {args.policy} · "
              f"{'  '.join(picks)}{X}")
        if (when := llm_catalog.verified_on()):
            print(f" {D}     catalog prices verified {when}; "
                  f"MISHNE_MODEL_CATALOG overrides{X}")
    else:
        print(f" {Y}no vendor API key — deterministic path only. Set any of "
              f"ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, "
              f"XAI_API_KEY.{X}")

    # 0-4, per asset, cached ------------------------------------------------
    assets: list[project.AssetIngest] = []
    for i, media in enumerate(args.media):
        if not media.exists():
            print(f"  {R}{media} does not exist{X}")
            return 1
        tag = f"{i + 1}/{len(args.media)}" if len(args.media) > 1 else "  "
        print(f"\n {B}{tag} {media.name}{X}")
        try:
            ing = project.ingest(
                media, work, language=args.language, provider=args.asr,
                ledger=router.ledger,
                replay=args.replay, model=args.model,
                model_path=args.model_path, assume_rate=assume_rate(args.rate),
                diarize_models=args.diarize,
                on_progress=lambda m: print(f"      {D}{m}{X}"))
        except Exception as exc:  # noqa: BLE001
            print(f"      {R}{type(exc).__name__}: {exc}{X}")
            if args.asr == "faster-whisper" and (
                    "model" in str(exc).lower() or "connect" in str(exc).lower()):
                print(f"\n  {Y}If this is a network error the Whisper model "
                      f"could not be downloaded.\n  Allowlist huggingface.co, "
                      f"or fetch it once and pass --model-path.{X}")
            return 1
        durs = sorted(b.duration_ms for b in ing.beats)
        median_s = (durs[len(durs) // 2] / 1000) if durs else 0
        kind = "already cut" if ing.is_sequence else "rushes"
        print(f"      {ing.rate} · start {frames_to_tc(ing.start_tc_frames, ing.rate)}"
              f" · {ing.duration_s / 60:.1f} min · {ing.language} · {kind}")
        print(f"      {len(ing.beats)} beats · median {median_s:.1f}s")
        for w in ing.warnings:
            print(f"      {Y}{w}{X}")
        assets.append(ing)

    beats = [b for a in assets for b in a.beats]
    order = project.asset_order(assets)
    ctxs = project.contexts(assets)
    if not beats:
        print(f"\n  {R}nothing transcribed — nothing to cut{X}")
        return 1

    # Language of the job is the language of most of its material. Mixed-
    # language projects are real, and the brief has to pick one.
    lang = max({a.language for a in assets},
               key=lambda L: sum(a.duration_s for a in assets
                                 if a.language == L))
    if len({a.language for a in assets}) > 1:
        print(f"\n {Y}uploads are not all in the same language — "
              f"briefing in {lang}{X}")
    if is_rtl_language(lang):
        print(f" {D}right-to-left language — transcript page renders RTL{X}")

    # speakers ---------------------------------------------------------------
    try:
        merges = project.parse_merges(args.merge_speakers)
    except ValueError as exc:
        print(f"  {R}{exc}{X}")
        return 1
    speakers = project.unify_speakers(assets, merges)
    names = {s.id: s.display for s in speakers}
    if speakers:
        print(f"\n    speakers     {len(speakers)} · {', '.join(names.values())}")
    else:
        print(f"\n    speakers     {Y}not separated{X}")
    for a in assets:
        for n in a.attribution.notes:
            print(f"                 {D}{n}{X}")
        if not a.attribution.reliable and a.attribution.speakers:
            print(f"                 {Y}speaker labels on {a.path.name} are "
                  f"not reliable — check before trusting the cut{X}")
    if len(assets) > 1 and not merges:
        print(f"                 {D}the same person in two uploads is two "
              f"speakers here. --merge-speakers to join them.{X}")

    # 5 ----------------------------------------------------------------------
    ed = brief_step.compile_brief(
        args.notes, parse_target(args.target),
        use_llm=(args.scorer != "heuristic"), router=router, language=lang,
        handle_frames=args.handles)
    print(f"  5 brief        target {ed.target_duration_s}s ±"
          f"{ed.duration_tolerance_s}s · {ed.narrative_shape}")
    for c in ed.clarifications:
        print(f"                 {D}{c}{X}")

    # 6 ----------------------------------------------------------------------
    # Candidate spans. A long block becomes several offers, every boundary
    # gated on real silence — see steps/propose.py for why that gate is the
    # point of the stage.
    speech_by_asset = {a.asset_id: a.speech for a in assets}
    if args.whole_cut:
        if router is None or not router.available_for("spans"):
            print(f"  6 whole cut    {R}needs a vendor key{X}")
            return 1
        from mishne.pipeline.steps import wholecut

        candidates, provided_scores = wholecut.propose_cut(
            beats, speech_by_asset.get, ed, router)
        chosen = sum(1 for v in provided_scores.values() if v > 0)
        print(f"  6 whole cut    {chosen} spans chosen from {len(beats)} beats "
              f"in one pass · {wholecut.propose_cut.refused} of "
              f"{wholecut.propose_cut.offered} refused by the silence gate")
    else:
        provided_scores = None
        proposer = (None if args.spans == "none"
                    else propose.get_proposer(args.spans, router))
        candidates = propose.build(beats, speech_by_asset.get, ed, proposer)
    carved = 0 if args.whole_cut else getattr(propose.build, "carved", 0)
    fell_back = [] if args.whole_cut else getattr(propose.build, "failed", [])
    if fell_back:
        # Loud, because a silent fallback is indistinguishable from a model
        # deciding a beat is not worth carving, and the difference is the whole
        # quality of the cut.
        kinds = ", ".join(sorted(set(fell_back)))
        print(f"                 {R}{len(fell_back)} of {len(beats)} beats "
              f"fell back to the whole block ({kinds}) — those were not "
              f"carved{X}")
    if carved:
        longest = max((b.duration_ms for b in beats), default=0) / 1000
        print(f"  6 spans        {len(candidates)} candidates from "
              f"{len(beats)} beats · {carved} carved out of long blocks "
              f"(longest was {longest:.0f}s)")
        if proposer is None:
            print(f"                 {D}enumerated between cut points — no "
                  f"judgement about which span is a thought. Set any vendor "
                  f"API key for that.{X}")

    if provided_scores is not None:
        # Stage 7 already happened: the same call that chose the spans scored
        # them, because "is this worth its seconds" is not a separable question
        # once you have read the whole piece.
        scores = provided_scores
        scorer = type("Provided", (), {"name": "whole-cut"})()
    else:
        scorer = score_step.get_scorer(args.scorer, router)
        scores = scorer.score(candidates, ed)
    scores = score_step.apply_disqualifiers(candidates, scores, ed.keep_filler)
    live = sum(1 for v in scores.values() if v > 0)
    print(f"  7 score        {scorer.name} · {live} of {len(candidates)} eligible")
    if scorer.name == "heuristic":
        print(f"                 {Y}control scorer — proves the plumbing, not "
              f"the cut. Do not show this to an editor as the product.{X}")

    # 7 ----------------------------------------------------------------------
    picks = select.solve(candidates, scores, ed, order)
    if not picks:
        print(f"  7 select       {R}nothing selected — target may be "
              f"unreachable with the available material{X}")
        return 1
    picked_s = sum(p.beat.duration_ms for p in picks) / 1000
    used_assets = {p.beat.asset_id for p in picks}
    spread = ("" if len(assets) == 1
              else f" · from {len(used_assets)} of {len(assets)} uploads")
    trimmed = sum(1 for p in picks if p.beat.kind != "beat")
    print(f"  8 select       {len(picks)} spans · {picked_s:.0f}s "
          f"({picked_s - ed.target_duration_s:+.0f}s vs target){spread}"
          + (f" · {trimmed} cut inside a beat" if trimmed else ""))
    # The failure that made a "forty second cut" the first forty seconds
    # verbatim. Nothing errored; the beats were simply too big to choose from.
    med = sorted(b.duration_ms for b in candidates)[len(candidates) // 2] / 1000
    if med > ed.target_duration_s / 4:
        print(f"                 {Y}beats average {med:.0f}s against a "
              f"{ed.target_duration_s:.0f}s target — too few pieces to shape a "
              f"cut. This will be a chop, not an edit.{X}")

    # 9 ----------------------------------------------------------------------
    cuts = refine.refine_multi(picks, ctxs, handle_frames=ed.handle_frames)
    warned = sum(1 for c in cuts if c.warnings)
    print(f"  9 refine       {len(cuts)} clips"
          + (f" · {warned} with notes" if warned else ""))

    # 10 ---------------------------------------------------------------------
    refs = project.asset_refs(assets)
    stem = first.stem if len(args.media) == 1 else f"{first.stem}_project"
    timeline = assemble.build_multi(cuts, refs, name=f"{stem}_roughcut")
    seq_rate = assets[0].rate
    emitted = len(list(timeline.tracks[0].find_clips()))
    extra = ("" if emitted == len(cuts)
             else f" · {emitted - len(cuts)} split across source joins")
    total = sum(c.frames / ctxs[c.asset_id].rate.fps for c in cuts)
    print(f" 10 assemble     {emitted} clips · {total:.1f}s · "
          f"record {frames_to_tc(round(timeline.global_start_time.value), seq_rate)}"
          f"{extra}")
    for w in assemble.warnings_for(refs):
        print(f"                 {Y}{w}{X}")
    if any(a.is_aaf for a in assets):
        print(f"                 {D}source mob IDs inherited — this will "
              f"relink in the original project{X}")

    # 11 ---------------------------------------------------------------------
    artifacts = emit.emit(timeline, out, stem)
    for a in artifacts:
        if a.ok:
            print(f" 11 emit         {G}{a.fmt:7}{X} {a.bytes:>9,} B  "
                  f"{D}{a.target_nle}{X}")
        else:
            print(f" 11 emit         {R}{a.fmt:7} {a.error}{X}")

    # 12 ---------------------------------------------------------------------
    checks = validate.validate(timeline, artifacts, seq_rate)
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

    # transcript -------------------------------------------------------------
    asset_names = {a.asset_id: a.path.name for a in assets}
    transcript_page.render(
        beats, cuts, ed, seq_rate, assets[0].start_tc_frames,
        assets[0].duration_frames, names, title, lang,
        out / f"{stem}.transcript.html",
        contexts=ctxs, asset_names=asset_names)

    (out / f"{stem}.mishne.json").write_text(json.dumps({
        "assets": [{
            "assetId": a.asset_id, "media": a.path.name,
            "rate": {"num": a.rate.num, "den": a.rate.den,
                     "dropFrame": a.rate.drop_frame},
            "startTc": frames_to_tc(a.start_tc_frames, a.rate),
            "durationFrames": a.duration_frames, "language": a.language,
            "isAaf": a.is_aaf, "beats": len(a.beats),
        } for a in assets],
        "language": lang,
        "brief": ed.to_dict(),
        "scorer": scorer.name,
        # The reproducibility contract. A job that fell over to a second vendor
        # mid-way was produced by both, and this has to say so.
        "modelVersions": router.ledger.models_used(),
        "llmCalls": [c.to_dict() for c in router.ledger.calls],
        "llmCostUsd": round(router.ledger.cost_usd, 6),
        "speakers": [s.to_dict() for s in speakers],
        "cuts": [{
            "beatId": c.beat_id, "parentId": c.parent_id,
            "assetId": c.asset_id, "order": c.order_idx,
            "tcIn": frames_to_tc(c.src_in, ctxs[c.asset_id].rate),
            "tcOut": frames_to_tc(c.src_out, ctxs[c.asset_id].rate),
            "frames": c.frames, "speaker": names.get(c.speaker, c.speaker),
            "score": round(c.score, 1), "rationale": c.rationale,
            "warnings": c.warnings, "text": c.text} for c in cuts],
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    if router.ledger.calls:
        print()
        for line in router.ledger.summary():
            print(f"    llm          {D}{line}{X}")
        fell = [c for c in router.ledger.calls if c.fell_back_from]
        for c in fell:
            print(f"                 {Y}{c.task}: fell back from "
                  f"{c.fell_back_from} to {c.provider}/{c.model}{X}")
        print(f"    llm          {B}${router.ledger.cost_usd:.4f}{X} total")

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
