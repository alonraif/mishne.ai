"""What each step actually does — the registry's names, bound to the pipeline.

`pipeline/steps/__init__.py` declares the stages; this binds each name to the
function that runs it. They are kept apart because the declaration is what the
state machine is generated from and what the UI renders, and it must be
readable without dragging in ffmpeg, OpenTimelineIO and a solver.

**`run.py` is the specification.** Every step below calls the same function the
concierge CLI calls, in the same order, with the same arguments. Where that was
one long script, this is the same script cut at the joints the orchestrator
needs — which is why the per-asset stages were extracted into
`project.stage_*` rather than reimplemented here. Two drivers, one
implementation; a second idea of what "structure into beats" means is how a
re-run silently stops matching the reference run.

The per-asset phase is cached on the asset's content and survives every re-run
(ADR-0008). The per-job phase is cheap by comparison, with one exception: the
three stages that call a model. Their outputs are written to the job's working
directory as they are produced, so a worker that dies after scoring does not
pay for scoring twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..pipeline import project
from ..pipeline.steps import (
    assemble,
    brief as brief_step,
    emit,
    propose,
    refine,
    score as score_step,
    select,
    structure,
    transcript_page,
    validate,
)
from ..pipeline.steps.base import StepContext
from ..timecode import Rate, frames_to_tc

#: Bumped when a cached model output stops being readable by this code. Same
#: idea as `project.CACHE_VERSION`, for the job phase.
JOB_CACHE_VERSION = 1


@dataclass
class AssetSource:
    """One upload, as the runner is handed it.

    `path` is a real file on a real disk by the time a step sees it: ffmpeg
    takes argv and pyaaf2 seeks around inside structured storage, so the
    workspace has already staged it (ADR-0013).

    **Two ids, deliberately.** `asset_id` is the database row — what a job's
    progress is reported against, and what the API can look up. `content_id` is
    the content hash, and it is what the pipeline uses: it names the ingest
    cache, and it is the id that ends up inside a beat, a cut and every
    artifact. Keying the cache on the row id would transcribe the same rushes
    once per project, which is exactly the economics ADR-0008 exists to
    protect; keying the artifacts on the row id would make two identical cuts
    from the same footage incomparable.
    """

    asset_id: str
    path: Path
    #: Defaults to the digest of the file, which is what `run.py` does.
    content_id: str = ""
    #: Companion media for a linked AAF, materialised beside it under their own
    #: names — which is the whole of the resolution (ADR-0014).
    companions: list[Path] = field(default_factory=list)
    #: For audio-only uploads, which carry no frame rate of their own (ADR-0005).
    assume_rate: Rate | None = None

    @property
    def pipeline_id(self) -> str:
        """The id the pipeline uses. Computed once, from the bytes."""
        if not self.content_id:
            self.content_id = project.asset_id_for(self.path)
        return self.content_id


@dataclass
class JobRequest:
    """Everything a job needs that is not an asset."""

    job_id: str
    org_id: str
    project_id: str
    assets: list[AssetSource]
    out_dir: Path
    work_dir: Path
    notes: str = ""
    target_duration_s: int | None = None
    mode: str = "ai"
    handle_frames: int = 6
    language: str | None = None
    #: "auto" | "heuristic" | "model". The heuristic scorer proves the plumbing,
    #: not the cut.
    scorer: str = "auto"
    #: "auto" | "enumerate" | "none" — how candidate spans are proposed.
    spans: str = "auto"
    stem: str = "roughcut"
    #: What the transcript page is headed with. Empty means what `run.py` uses:
    #: the upload's own name, or "first + N more" for a multi-asset job. It is a
    #: heading a person reads, so it is the customer's filename rather than an
    #: id — and it is the one place a filename legitimately appears in output.
    title: str = ""
    # Transcription inputs, exactly as run.py takes them.
    asr_provider: str = "faster-whisper"
    model: str = "base"
    model_path: str | None = None
    replay: Path | None = None
    diarize_models: Path | None = None
    router: object = None


@dataclass
class AssetRun:
    """Per-asset scratch, one stage at a time."""

    source: AssetSource
    adir: Path
    prepared: object = None
    tracks: list = field(default_factory=list)
    asr: object = None
    speech: object = None
    attribution: object = None
    beats: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    ingest: object = None
    #: True when the whole per-asset phase came from cache — which is what makes
    #: "add a reel and re-cut" cheap, and what "zero transcription on a re-run"
    #: means in practice.
    from_cache: bool = False


@dataclass
class RunState:
    """Everything the pipeline builds, in the order it builds it."""

    request: JobRequest
    runs: dict[str, AssetRun] = field(default_factory=dict)
    #: The asset the per-asset phase is currently on.
    current: str = ""
    assets: list = field(default_factory=list)          # AssetIngest, in order
    beats: list = field(default_factory=list)
    order: dict = field(default_factory=dict)
    contexts: dict = field(default_factory=dict)
    language: str = "en"
    speakers: list = field(default_factory=list)
    names: dict = field(default_factory=dict)
    brief: object = None
    candidates: list = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    scorer_name: str = ""
    picks: list = field(default_factory=list)
    cuts: list = field(default_factory=list)
    timeline: object = None
    artifacts: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    carved: int = 0

    @property
    def run(self) -> AssetRun:
        return self.runs[self.current]

    @property
    def job_dir(self) -> Path:
        d = self.request.work_dir / "jobs" / self.request.job_id
        d.mkdir(parents=True, exist_ok=True)
        return d


# ─────────────────────────────────────────────────────────── the asset phase


def step_prepare(ctx: StepContext, state: RunState) -> str:
    run = state.run
    if run.ingest is not None:  # served from cache by the runner
        return "cached"
    run.prepared = project.stage_prepare(
        run.source.path, run.adir, assume_rate=run.source.assume_rate,
        on_progress=ctx.on_progress,
    )
    info = run.prepared.info
    kind = "sequence" if run.prepared.aaf is not None else "media"
    return f"{kind} · {info.rate} · {info.duration_frames} frames"


def step_audio(ctx: StepContext, state: RunState) -> str:
    run = state.run
    if run.ingest is not None:
        return "cached"
    run.tracks = project.stage_audio(run.prepared, run.adir)
    return f"{len(run.tracks)} track(s)"


def step_transcribe(ctx: StepContext, state: RunState) -> str:
    run = state.run
    if run.ingest is not None:
        # The stage the cache exists for. A re-run of a job whose assets have
        # not changed must never pay for this twice (ADR-0008).
        return "cached"
    req = state.request
    run.asr = project.stage_transcribe(
        run.tracks, run.adir, provider=req.asr_provider, language=req.language,
        replay=req.replay, model=req.model, model_path=req.model_path,
    )
    return f"{len(run.asr.words)} words · {run.asr.language}"


def step_vad(ctx: StepContext, state: RunState) -> str:
    run = state.run
    if run.ingest is not None:
        return "cached"
    run.speech = project.stage_vad(run.tracks)
    return f"{len(run.speech.speech)} speech segments"


def step_structure(ctx: StepContext, state: RunState) -> str:
    run = state.run
    if run.ingest is not None:
        return "cached"
    run.beats, run.warnings = project.stage_structure(
        run.asr, run.speech, run.tracks, run.source.pipeline_id, run.prepared.seams
    )
    return f"{len(run.beats)} beats"


def step_speakers(ctx: StepContext, state: RunState) -> str:
    """The last per-asset stage, and where the cache is written."""
    run = state.run
    if run.ingest is not None:
        state.assets.append(run.ingest)
        return "cached"

    req = state.request
    run.attribution = project.stage_speakers(
        run.asr, run.tracks, run.prepared,
        diarize_models=req.diarize_models, on_progress=ctx.on_progress,
    )
    info = run.prepared.info
    run.ingest = project.AssetIngest(
        asset_id=run.source.pipeline_id,
        path=run.source.path,
        rate=info.rate,
        start_tc_frames=info.start_tc_frames,
        duration_frames=info.duration_frames,
        language=run.asr.language,
        beats=run.beats,
        speakers=run.attribution.speakers,
        attribution=run.attribution,
        speech=run.speech,
        audio_path=run.tracks[0].path,
        aaf=run.prepared.aaf,
        audio_tracks=len(run.tracks),
        provenance=run.prepared.provenance,
        seams=run.prepared.seams,
        warnings=run.warnings,
    )
    project.finish_ingest(run.adir, run.ingest)
    state.assets.append(run.ingest)
    n = len(run.attribution.speakers)
    return f"{n} speaker(s)" if n else "not separated"


# ───────────────────────────────────────────────────────────── the job phase


def _gather(state: RunState) -> None:
    """Fold the per-asset results into one job. Cheap, and done once."""
    if state.beats:
        return
    assets = state.assets
    state.beats = [b for a in assets for b in a.beats]
    state.order = project.asset_order(assets)
    state.contexts = project.contexts(assets)
    # The language of a job is the language of most of its material. Mixed
    # projects are real, and the brief has to pick one.
    state.language = max(
        {a.language for a in assets},
        key=lambda L: sum(a.duration_s for a in assets if a.language == L),
    )
    state.speakers = project.unify_speakers(assets, {})
    state.names = {s.id: s.display for s in state.speakers}


def step_brief(ctx: StepContext, state: RunState) -> str:
    """Stage 5. One model call, with a deterministic fallback.

    Cached on disk: a worker that dies during scoring must not pay for the brief
    again, and the brief is what everything downstream is shaped by.
    """
    _gather(state)
    if not state.beats:
        raise ValueError("nothing transcribed — nothing to cut")
    req = state.request
    cache = state.job_dir / "brief.json"
    cached = _read_cache(cache)
    if cached is not None:
        state.brief = brief_step.EditBrief(**cached["brief"])
        return "cached"

    state.brief = brief_step.compile_brief(
        req.notes,
        req.target_duration_s,
        use_llm=(req.scorer != "heuristic"),
        router=req.router,
        language=state.language,
        handle_frames=req.handle_frames,
    )
    _write_cache(cache, {"brief": state.brief.to_dict()})
    ed = state.brief
    return f"target {ed.target_duration_s}s ±{ed.duration_tolerance_s}s · {ed.narrative_shape}"


def step_propose(ctx: StepContext, state: RunState) -> str:
    """Stage 6. Candidate spans, every boundary gated on real silence.

    One model call per long beat when a model is proposing — 35 on a 26-minute
    interview, and they are independent. They are run inside this step rather
    than fanned out into the state machine: the fan-out multiplies state
    transitions and cost for a stage nobody has profiled, and `llm/router.py`
    already fails over across vendors, so a second retry layer needs care.
    """
    req = state.request
    speech_by_asset = {a.asset_id: a.speech for a in state.assets}
    cache = state.job_dir / "candidates.json"
    cached = _read_cache(cache)
    if cached is not None:
        state.candidates = [_beat_from_dict(b) for b in cached["candidates"]]
        state.carved = cached.get("carved", 0)
        return "cached"

    proposer = None if req.spans == "none" else propose.get_proposer(req.spans, req.router)
    state.candidates = propose.build(
        state.beats, speech_by_asset.get, state.brief, proposer
    )
    state.carved = getattr(propose.build, "carved", 0)
    _write_cache(cache, {
        "candidates": [_beat_to_dict(b) for b in state.candidates],
        "carved": state.carved,
    })
    return f"{len(state.candidates)} candidates from {len(state.beats)} beats"


def step_score(ctx: StepContext, state: RunState) -> str:
    """Stage 7. The model scores; it never decides a duration or a frame."""
    req = state.request
    cache = state.job_dir / "scores.json"
    cached = _read_cache(cache)
    if cached is not None:
        state.scores = {k: float(v) for k, v in cached["scores"].items()}
        state.scorer_name = cached.get("scorer", "")
        return "cached"

    scorer = score_step.get_scorer(req.scorer, req.router)
    scores = scorer.score(state.candidates, state.brief)
    state.scores = score_step.apply_disqualifiers(
        state.candidates, scores, state.brief.keep_filler
    )
    state.scorer_name = scorer.name
    _write_cache(cache, {"scores": state.scores, "scorer": scorer.name})
    live = sum(1 for v in state.scores.values() if v > 0)
    return f"{scorer.name} · {live} of {len(state.candidates)} eligible"


def step_select(ctx: StepContext, state: RunState) -> str:
    """Stage 8. A solver, not a model: the LLM scores and CP-SAT chooses
    (ADR-0004). Deterministic — same inputs, identical picks."""
    state.picks = select.solve(state.candidates, state.scores, state.brief, state.order)
    if not state.picks:
        raise ValueError(
            "nothing selected — the target may be unreachable with this material"
        )
    picked_s = sum(p.beat.duration_ms for p in state.picks) / 1000
    return f"{len(state.picks)} spans · {picked_s:.0f}s"


def step_refine(ctx: StepContext, state: RunState) -> str:
    """Stage 9. Runs in every mode: a hand-marked cut still gets silence
    snapping, handles and frame quantization. The user picks what; stage 9
    decides where."""
    state.cuts = refine.refine_multi(
        state.picks, state.contexts, handle_frames=state.brief.handle_frames
    )
    warned = sum(1 for c in state.cuts if c.warnings)
    return f"{len(state.cuts)} clips" + (f" · {warned} with notes" if warned else "")


def step_assemble(ctx: StepContext, state: RunState) -> str:
    refs = project.asset_refs(state.assets)
    state.timeline = assemble.build_multi(
        state.cuts, refs, name=f"{state.request.stem}_roughcut"
    )
    emitted = len(list(state.timeline.tracks[0].find_clips()))
    return f"{emitted} clips"


def step_emit(ctx: StepContext, state: RunState) -> str:
    out = state.request.out_dir
    out.mkdir(parents=True, exist_ok=True)
    state.artifacts = emit.emit(state.timeline, out, state.request.stem)
    ok = sum(1 for a in state.artifacts if a.ok)
    return f"{ok} of {len(state.artifacts)} formats"


def step_validate(ctx: StepContext, state: RunState) -> str:
    """Stage 12. Reads every artifact back and compares it to the timeline.

    A failure here is not retried: it means an artifact is wrong, and writing it
    again produces the same wrong artifact.
    """
    seq_rate = state.assets[0].rate
    state.checks = validate.validate(state.timeline, state.artifacts, seq_rate)
    failed = [c for c in state.checks if not c.ok]
    if failed:
        raise ValueError(
            f"{len(failed)} artifact(s) failed validation: "
            + ", ".join(c.fmt for c in failed)
        )
    return f"{len(state.checks)} formats validated"


def step_transcript_page(ctx: StepContext, state: RunState) -> str:
    """The transcript, and the manifest that says how the cut was made.

    Not decoration: it is what an editor reads to decide whether to trust the
    cut, and the manifest is the reproducibility contract — which models ran,
    in what order, and what they cost (ADR-0011).
    """
    req = state.request
    out = req.out_dir
    seq_rate = state.assets[0].rate
    asset_names = {a.asset_id: a.path.name for a in state.assets}
    title = req.title or (
        state.assets[0].path.name if len(state.assets) == 1
        else f"{state.assets[0].path.name} + {len(state.assets) - 1} more"
    )
    transcript_page.render(
        state.beats, state.cuts, state.brief, seq_rate,
        state.assets[0].start_tc_frames, state.assets[0].duration_frames,
        state.names, title, state.language,
        out / f"{req.stem}.transcript.html",
        contexts=state.contexts, asset_names=asset_names,
    )
    ledger = getattr(req.router, "ledger", None)
    (out / f"{req.stem}.mishne.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "assetId": a.asset_id, "media": a.path.name,
                        "rate": {"num": a.rate.num, "den": a.rate.den,
                                 "dropFrame": a.rate.drop_frame},
                        "startTc": frames_to_tc(a.start_tc_frames, a.rate),
                        "durationFrames": a.duration_frames,
                        "language": a.language, "isAaf": a.is_aaf,
                        "beats": len(a.beats),
                    }
                    for a in state.assets
                ],
                "language": state.language,
                "brief": state.brief.to_dict(),
                "scorer": state.scorer_name,
                "modelVersions": ledger.models_used() if ledger else {},
                "llmCalls": [c.to_dict() for c in ledger.calls] if ledger else [],
                # `0` rather than `0.0` when there is no router: the manifest
                # is compared byte for byte against the reference run, and
                # json.dumps writes those two differently.
                "llmCostUsd": round(ledger.cost_usd, 6) if ledger else 0,
                "speakers": [s.to_dict() for s in state.speakers],
                "cuts": [
                    {
                        "beatId": c.beat_id, "parentId": c.parent_id,
                        "assetId": c.asset_id, "order": c.order_idx,
                        "tcIn": frames_to_tc(c.src_in, state.contexts[c.asset_id].rate),
                        "tcOut": frames_to_tc(c.src_out, state.contexts[c.asset_id].rate),
                        "frames": c.frames,
                        "speaker": state.names.get(c.speaker, c.speaker),
                        "score": round(c.score, 1), "rationale": c.rationale,
                        "warnings": c.warnings, "text": c.text,
                    }
                    for c in state.cuts
                ],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return "transcript and manifest written"


#: Every step in the registry, bound to what runs it. `test_step_contract.py`
#: asserts this covers the registry exactly — a name in one and not the other is
#: either a phantom state in the machine or a stage nobody can execute.
IMPLEMENTATIONS = {
    "prepare": step_prepare,
    "audio": step_audio,
    "transcribe": step_transcribe,
    "vad": step_vad,
    "structure": step_structure,
    "speakers": step_speakers,
    "brief": step_brief,
    "propose": step_propose,
    "score": step_score,
    "select": step_select,
    "refine": step_refine,
    "assemble": step_assemble,
    "emit": step_emit,
    "validate": step_validate,
    "transcript_page": step_transcript_page,
}


# ─────────────────────────────────────────────────────────────── the caches


def _write_cache(path: Path, payload: dict) -> None:
    payload = {"version": JOB_CACHE_VERSION, **payload}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _read_cache(path: Path) -> dict | None:
    """A cached model output, or None if this code would build it differently."""
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a stale cache is not worth dying for
        return None
    return payload if payload.get("version") == JOB_CACHE_VERSION else None


def _beat_to_dict(b) -> dict:
    """A candidate span, as the job cache stores it.

    The same fields the ingest cache keeps, plus the span provenance stage 6
    adds — `parent_id`, `kind` and the rationale are what the transcript page
    shows to explain why a span was cut where it was.
    """
    return {
        "id": b.id, "idx": b.idx, "assetId": b.asset_id, "speaker": b.speaker,
        "startMs": b.start_ms, "endMs": b.end_ms, "text": b.text,
        "flags": b.flags, "confidence": round(b.mean_confidence, 3),
        "parentId": b.parent_id, "kind": b.kind, "rationale": b.rationale,
    }


def _beat_from_dict(d: dict):
    return structure.Beat(
        id=d["id"], idx=d["idx"], asset_id=d["assetId"], speaker=d["speaker"],
        start_ms=d["startMs"], end_ms=d["endMs"], text=d["text"],
        flags=d["flags"], mean_confidence=d.get("confidence", 1.0),
        parent_id=d.get("parentId", ""), kind=d.get("kind", "beat"),
        rationale=d.get("rationale", ""),
    )
