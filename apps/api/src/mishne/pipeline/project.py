"""Projects: many uploads, many outputs.

A media project is long. Footage arrives over weeks, and one finished piece is
cut from several sessions. The single-asset pipeline could cut ten pieces from
one interview but not one piece from three, which is the wrong way round for how
this work actually happens.

## The seam

The pipeline splits cleanly, and the split is what makes separated uploads
natural rather than awkward:

    per ASSET, once, cached forever    stages 0-4 and speaker attribution
    per JOB, across chosen assets      stages 5-8  (brief, score, select)
    per JOB, mapping back per asset    stages 9-12 (refine, assemble, emit)

Transcription is the expensive step and it belongs to the asset, not the job.
An upload transcribed today is reused by a job next month at no cost. That is
also why `AssetIngest` is written to disk in full: re-running a job must never
re-transcribe.

## No virtual timeline

The obvious approach — lay the assets end to end and give every beat a global
position — is a trap. Cut-point refinement needs each asset's own silence map,
and assembly needs each asset's own timecode and frame rate. Fabricating global
coordinates means converting back at every step and getting it wrong once.

So beats keep their own asset's local timing and carry `asset_id`. Ordering
across assets uses `(asset order, start)`, which is all "chronological" can
honestly mean when the material was shot on different days.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..asr import ASRResult
from ..asr.base import Word
from ..timecode import Rate
from .steps import aaf_ingest, audio as audio_step, prepare, speakers as spk
from ..asr.base import DEFAULT_PROVIDER
from .steps import structure, transcribe, vad
from .steps.structure import Beat
from .steps.vad import SpeechMap

# Bump when anything that shapes a cached ingest changes — segmentation rules,
# speaker attribution, what gets stored. A stale cache is worse than a slow
# one: it serves beats built by code that no longer exists, and the only symptom
# is a cut that looks subtly wrong. Transcription is keyed separately and is not
# repaid by a bump here.
#: Bumped to 3 when the cache started carrying each beat's words. A cache
#: written by version 2 has no words in it, and a beat without words cannot be
#: carved into spans — so an old entry has to be rebuilt rather than served.
CACHE_VERSION = 3


@dataclass
class AssetIngest:
    """Everything stages 0-4 produce for one upload. Cached; computed once."""

    asset_id: str
    path: Path
    rate: Rate
    start_tc_frames: int
    duration_frames: int
    language: str
    beats: list[Beat]
    speakers: list[spk.Speaker]
    attribution: spk.Attribution
    speech: SpeechMap | None
    audio_path: Path | None
    aaf: aaf_ingest.AAFSource | None = None
    audio_tracks: int = 1
    # "rushes" — a continuous recording nobody has cut yet. "sequence" — an AAF
    # or EDL that has already been through an edit, whose existing cuts are
    # evidence about where the beats are. See steps/structure.py.
    provenance: str = "rushes"
    seams: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Which engine produced the transcript, for the `transcripts` row and the
    #: reproducibility record. Defaulted and therefore last: a cache written
    #: before these existed loads with them empty, which is the honest answer —
    #: bumping CACHE_VERSION to learn a display field would re-transcribe every
    #: asset in the system to fill in a string.
    asr_provider: str = ""
    asr_model: str = ""

    @property
    def duration_s(self) -> float:
        return self.duration_frames / self.rate.fps

    @property
    def is_aaf(self) -> bool:
        return self.aaf is not None

    @property
    def is_sequence(self) -> bool:
        return self.provenance == "sequence"

    def speaker_name(self, speaker_id: str) -> str:
        for s in self.speakers:
            if s.id == speaker_id:
                return s.display
        return speaker_id


def asset_id_for(path: Path, content_hash: str | None = None) -> str:
    """A stable id for an upload. Content-addressed.

    This used to be filename plus size, with a comment saying a content hash was
    the right answer and was deferred because it costs a full read of a very
    large file. Real storage is where that stops being true: the browser has to
    read every byte to upload them, so it hashes on the way past and sends the
    digest with the request — `content_hash` is that digest, and no extra read
    happens at all.

    Why it matters more than tidiness. The id is the **cache key for stages
    0-4**, transcription included. Under filename-plus-size, a re-export of the
    same interview that happens to land on the same byte count — trivially
    possible with a re-render — reads a cache built from different audio, and
    the only symptom is a cut whose words do not match the picture. Under a
    content hash that cannot happen, and the pleasant converse falls out for
    free: the same rushes uploaded to two projects are transcribed once.

    Without a digest (the concierge CLI, pointed at a local file) it is computed
    here. That is one sequential read of the file, which is cheap next to
    everything that follows it.
    """
    if content_hash is None:
        from ..storage import sha256_file
        content_hash = sha256_file(path)
    return f"a_{content_hash.lower()[:24]}"


class _DirWorkspace:
    """A plain directory, wearing the `Workspace` shape.

    `ingest` takes either a `Path` — the concierge CLI on one machine — or a
    `mishne.workspace.Workspace`. Rather than branch on the type at four call
    sites, the Path is wrapped once here. Deliberately duck-typed rather than
    importing the protocol: `mishne.workspace` imports boto3, and the pipeline
    has to keep running on a laptop with no cloud dependency at all.
    """

    def __init__(self, path: Path) -> None:
        self.root = Path(path)

    def asset_dir(self, asset_id: str) -> Path:
        d = self.root / "assets" / asset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def publish_asset(self, asset_id: str) -> None:
        return None


@dataclass
class Prepared:
    """What stage 0 established: the time base, and how to get at the audio.

    `aaf` is set when the upload is a sequence rather than a media file. That is
    a branch inside the stage, not a stage of its own — ffprobe cannot read an
    AAF at all, so the two paths diverge here and converge again immediately.
    """

    info: object                       # prepare.MediaInfo
    source: Path                       # what stage 1 extracts audio from
    aaf: object | None = None          # aaf_ingest.AAFSource
    provenance: str = "rushes"
    seams: list[int] = field(default_factory=list)


def stage_prepare(path: Path, adir: Path, assume_rate: Rate | None = None,
                  on_progress=None) -> Prepared:
    """Stage 0. Probe, and for a sequence, flatten it first.

    Called by `ingest` on one machine and by the orchestrator's per-asset step
    on a worker. One implementation: two drivers that disagree about what stage
    0 means is how a re-run stops matching the reference run.
    """
    say = on_progress or (lambda *_: None)
    path = Path(path)
    if path.suffix.lower() != ".aaf":
        return Prepared(info=prepare.probe(path, assume_rate=assume_rate), source=path)

    aaf = aaf_ingest.parse(path)
    say(f"AAF · {len(aaf.clips)} clips · {'embedded' if aaf.embedded else 'linked'}")
    flat = aaf_ingest.flatten_audio(aaf, adir)
    info = prepare.probe(flat, assume_rate=aaf.rate)
    # The sequence's own coordinates, not the flattened file's.
    info.start_tc_frames = aaf.start_tc_frames
    info.duration_frames = aaf.duration_frames
    # More than one clip means a person has already made cut decisions in this
    # material. Their positions on the flattened timeline, in ms — the boundary
    # between clips, not clip zero's start.
    seams = ([round(c.tl_in / aaf.rate.fps * 1000) for c in aaf.clips[1:]]
             if len(aaf.clips) > 1 else [])
    return Prepared(
        info=info, source=flat, aaf=aaf,
        provenance="sequence" if seams else "rushes", seams=seams,
    )


def stage_audio(prepared: Prepared, adir: Path):
    """Stage 1. One WAV per audio track, plus loudness."""
    tracks = audio_step.extract(prepared.info, adir)
    if not tracks:
        raise ValueError("no audio in this upload")
    return tracks


def stage_vad(tracks) -> object:
    """Stage 3, run before transcription because stage 4 needs both.

    Whisper's word timestamps are contiguous by construction — a gap between
    words is not silence — so the silence map has to come from the audio.
    """
    return vad.build(tracks[0].path)


def stage_transcribe(tracks, adir: Path, *, provider: str = DEFAULT_PROVIDER,
                     language: str | None = None, replay: Path | None = None,
                     model: str = "base", model_path: str | None = None,
                     ledger: object = None, keyterms: str = "") -> ASRResult:
    """Stage 2. The expensive one, and the one the cache exists for.

    The provider decides what the rest of the arguments mean, which is why they
    are split here rather than forwarded as one bag: `model`/`model_path` are
    Whisper's, and the managed engines take a ledger to record what the call
    cost and a work directory to split long audio into.
    """
    if replay:
        kwargs = {"path": replay}
    elif provider == "faster-whisper":
        kwargs = {"model": model, "model_path": model_path}
    else:
        kwargs = {"ledger": ledger, "keyterms": keyterms,
                  "work_dir": adir / "chunks"}
    return transcribe.run(
        tracks[0].path, adir,
        provider="replay" if replay else provider,
        language=language, **kwargs)


def stage_speakers(asr: ASRResult, tracks, prepared: Prepared, *,
                   diarize_models: Path | None = None, on_progress=None):
    """Who said what.

    Multi-track material needs no model: the loudest microphone is whoever is
    talking. One track needs diarization, and without it the honest answer is
    that the voices were never separated — not a confident "Speaker 1".
    """
    say = on_progress or (lambda *_: None)
    if len(tracks) > 1:
        return spk.attribute_from_files(
            asr.words, {t.track_index: t.path for t in tracks})
    if diarize_models:
        from ..diarize import get_provider
        say("separating voices")
        # The source clips are what keeps the clustering about people rather
        # than microphones — see diarize/sherpa_provider.py.
        seams = prepared.seams
        end = round(prepared.info.duration_frames / prepared.info.rate.fps * 1000)
        regions = ([(s0, s1) for s0, s1 in zip([0] + seams, seams + [end])]
                   if seams else None)
        dia = get_provider("sherpa-onnx", model_dir=Path(diarize_models)
                           ).diarize(tracks[0].path, regions=regions)
        attribution = spk.attribute_from_diarization(asr.words, dia)
        say(f"{len(attribution.speakers)} voice(s)"
            + ("" if attribution.reliable else " — separation is weak"))
        return attribution
    return spk.single_track_unseparated(asr.words)


def stage_structure(asr: ASRResult, speech, tracks, asset_id: str, seams: list[int]):
    """Stage 4. Beats, and whatever the segmentation wants to warn about."""
    beats = structure.build(asr.words, speech, language=asr.language,
                            loudness_lufs=tracks[0].integrated_lufs,
                            asset_id=asset_id, seams=seams)
    return beats, list(getattr(structure.build, "warnings", []))


def cached_ingest(adir: Path, path: Path, on_progress=None) -> AssetIngest | None:
    """The cache read, shared by both drivers.

    Stages 0-4 are keyed on the asset's content and survive every re-run, which
    is what makes "add a reel and re-cut" cheap and is the economics of the
    whole multi-upload feature (ADR-0008). `CACHE_VERSION` is why a worker
    running new segmentation code does not serve beats built by code that no
    longer exists.
    """
    say = on_progress or (lambda *_: None)
    cached = adir / "ingest.json"
    if not cached.exists():
        return None
    try:
        hit = _load(cached, path, adir)
        if hit is not None:
            return hit
        say("cache written by an older pipeline, re-ingesting")
    except Exception:  # noqa: BLE001 — a stale cache is not worth dying for
        say("cache unreadable, re-ingesting")
    return None


def finish_ingest(adir: Path, result: AssetIngest, ws=None) -> AssetIngest:
    """Write the cache and publish it. The end of the per-asset phase."""
    _save(result, adir / "ingest.json")
    if ws is not None:
        ws.publish_asset(result.asset_id)
    return result


def ingest(path: Path, work_dir, language: str | None = None,
           provider: str = DEFAULT_PROVIDER, replay: Path | None = None,
           model: str = "base", model_path: str | None = None,
           assume_rate: Rate | None = None, diarize_models: Path | None = None,
           on_progress=None, content_hash: str | None = None,
           ledger: object = None, keyterms: str = "") -> AssetIngest:
    """Stages 0-4 plus speaker attribution for one asset. Cached.

    `path` is a real file on a real disk, always — ffmpeg takes argv and pyaaf2
    seeks around inside structured storage, so neither can be handed a stream.
    When the source lives in S3, `mishne.workspace` has already put it on local
    disk and this function is none the wiser; see that module for why staging
    beat mounting.

    `work_dir` is either a `Path` (the concierge CLI) or a
    `mishne.workspace.Workspace` (a worker), which is what lets the cached
    ingest outlive the container that built it.

    The stages are the functions above, called in order. The orchestrator calls
    the same ones one at a time so it can record progress and resume between
    them (`orchestration/graph.py`); this is the single-machine driver, and the
    two must never grow separate ideas of what a stage does.
    """
    path = Path(path)
    ws = work_dir if hasattr(work_dir, "asset_dir") else _DirWorkspace(Path(work_dir))
    aid = asset_id_for(path, content_hash)
    adir = ws.asset_dir(aid)
    say = on_progress or (lambda *_: None)

    hit = cached_ingest(adir, path, say)
    if hit is not None:
        return hit

    prepared = stage_prepare(path, adir, assume_rate=assume_rate, on_progress=say)
    tracks = stage_audio(prepared, adir)
    speech = stage_vad(tracks)
    say(f"{len(speech.speech)} speech segments")
    asr = stage_transcribe(tracks, adir, provider=provider, language=language,
                           replay=replay, model=model, model_path=model_path,
                           ledger=ledger, keyterms=keyterms)
    say(f"{len(asr.words)} words · {asr.language}")
    attribution = stage_speakers(asr, tracks, prepared,
                                 diarize_models=diarize_models, on_progress=say)
    beats, warnings = stage_structure(asr, speech, tracks, aid, prepared.seams)
    say(f"{len(beats)} beats")

    info = prepared.info
    result = AssetIngest(
        asset_id=aid, path=path, rate=info.rate,
        start_tc_frames=info.start_tc_frames,
        duration_frames=info.duration_frames, language=asr.language,
        beats=beats, speakers=attribution.speakers, attribution=attribution,
        speech=speech, audio_path=tracks[0].path, aaf=prepared.aaf,
        audio_tracks=len(tracks), provenance=prepared.provenance,
        seams=prepared.seams, warnings=warnings,
        asr_provider=asr.provider, asr_model=asr.model,
    )
    return finish_ingest(adir, result, ws)


def _save(a: AssetIngest, path: Path) -> None:
    path.write_text(json.dumps({
        "cacheVersion": CACHE_VERSION,
        "assetId": a.asset_id,
        "path": str(a.path),
        "rate": {"num": a.rate.num, "den": a.rate.den,
                 "dropFrame": a.rate.drop_frame},
        "startTcFrames": a.start_tc_frames,
        "durationFrames": a.duration_frames,
        "language": a.language,
        "audioPath": str(a.audio_path) if a.audio_path else None,
        "isAaf": a.aaf is not None,
        "audioTracks": a.audio_tracks,
        "provenance": a.provenance,
        "asrProvider": a.asr_provider,
        "asrModel": a.asr_model,
        "seams": a.seams,
        "warnings": a.warnings,
        "speakers": [s.to_dict() for s in a.speakers],
        "beats": [{
            "id": b.id, "idx": b.idx, "assetId": b.asset_id,
            "speaker": b.speaker, "startMs": b.start_ms, "endMs": b.end_ms,
            "text": b.text, "flags": b.flags,
            "confidence": round(b.mean_confidence, 3),
            # The words, and they are not optional. Stage 6 carves a long beat
            # into candidate spans at word indices, gated on real silence
            # (ADR-0010) — `cut_points` returns indices into this list. A beat
            # restored without them has exactly two legal cut points, its own
            # edges, so nothing is ever carved and the scorer is offered whole
            # 27-second blocks instead of the thoughts inside them.
            #
            # This is what made a cached re-run produce a coarser cut than the
            # cold run that populated the cache, silently, and it is why the
            # model proposer made no calls at all on a cache hit: it builds its
            # prompt from these.
            "words": [{
                "t": w.text, "s": w.start_ms, "e": w.end_ms,
                "c": round(w.confidence, 3), "spk": w.speaker,
            } for w in b.words],
        } for b in a.beats],
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def _load(cached: Path, path: Path, adir: Path) -> AssetIngest | None:
    """The cached ingest, or None if a newer pipeline would build it differently."""
    d = json.loads(cached.read_text(encoding="utf-8"))
    if d.get("cacheVersion") != CACHE_VERSION:
        return None
    rate = Rate(d["rate"]["num"], d["rate"]["den"], d["rate"]["dropFrame"])
    beats = [Beat(id=b["id"], idx=b["idx"], asset_id=b["assetId"],
                  speaker=b["speaker"], start_ms=b["startMs"],
                  end_ms=b["endMs"], text=b["text"], flags=b["flags"],
                  mean_confidence=b.get("confidence", 1.0),
                  words=[Word(text=w["t"], start_ms=w["s"], end_ms=w["e"],
                              confidence=w.get("c", 1.0),
                              speaker=w.get("spk", ""))
                         for w in b.get("words", [])])
             for b in d["beats"]]
    speakers = [spk.Speaker(
        id=s["id"], source=s["source"], default_label=s["defaultLabel"],
        track_index=s.get("trackIndex"), word_count=s.get("wordCount", 0),
        speech_ms=s.get("speechMs", 0), confirmed=s.get("confirmed", False),
        label=s.get("label", "")) for s in d["speakers"]]

    audio_path = Path(d["audioPath"]) if d.get("audioPath") else None
    speech = vad.build(audio_path) if audio_path and audio_path.exists() else None
    aaf = aaf_ingest.parse(path) if d.get("isAaf") else None

    return AssetIngest(
        asset_id=d["assetId"], path=path, rate=rate,
        start_tc_frames=d["startTcFrames"],
        duration_frames=d["durationFrames"], language=d["language"],
        beats=beats, speakers=speakers,
        attribution=spk.Attribution(speakers=speakers),
        speech=speech, audio_path=audio_path, aaf=aaf,
        audio_tracks=d.get("audioTracks", 1),
        provenance=d.get("provenance", "rushes"), seams=d.get("seams", []),
        warnings=d.get("warnings", []),
        asr_provider=d.get("asrProvider", ""), asr_model=d.get("asrModel", ""),
    )


# --- from assets to one job --------------------------------------------------


def asset_order(assets: list[AssetIngest]) -> dict[str, int]:
    """Upload position by asset id — what "chronological" means across assets."""
    return {a.asset_id: i for i, a in enumerate(assets)}


def contexts(assets: list[AssetIngest]) -> dict[str, "refine.AssetContext"]:
    """Stage 9's view: each asset's own silence map, rate, timecode and extent."""
    from .steps import refine
    return {a.asset_id: refine.AssetContext(
        rate=a.rate, start_tc_frames=a.start_tc_frames,
        duration_frames=a.duration_frames, asset_id=a.asset_id,
        speech=a.speech, order=i) for i, a in enumerate(assets)}


def asset_refs(assets: list[AssetIngest]) -> dict[str, "assemble.AssetRef"]:
    """Stage 10's view: where each asset's frames actually live."""
    from .steps import assemble
    return {a.asset_id: assemble.AssetRef(
        rate=a.rate, start_tc_frames=a.start_tc_frames,
        duration_frames=a.duration_frames, asset_id=a.asset_id,
        media_path=None if a.is_aaf else a.path, aaf=a.aaf,
        audio_tracks=a.audio_tracks) for a in assets}


def unify_speakers(assets: list[AssetIngest],
                   merges: dict[str, str] | None = None) -> list[spk.Speaker]:
    """Give the job one speaker list, and mutate beats to match.

    **The same person in two uploads is two speakers until somebody says
    otherwise.** Attribution is per file: it knows which microphone a voice came
    down, and nothing at all about whether the person on track 1 of Tuesday's
    session is the person on track 1 of Friday's. Guessing they are the same
    reads as intelligence right up until the day it puts words in the wrong
    mouth in a delivered cut, and the customer has no way to tell it happened.

    So ids are namespaced by asset, which makes them safe by construction, and
    merging is something a human does — through `merges`, which the UI fills in
    from the speaker legend. A merge is cheap to make and impossible to detect
    after the fact, which is the right way round.

    A single-asset job is left exactly as it was: no namespacing, no relabelling.
    """
    if len(assets) <= 1:
        return list(assets[0].speakers) if assets else []

    merges = merges or {}
    short = {a.asset_id: Path(a.path).stem[:18] for a in assets}
    out: dict[str, spk.Speaker] = {}

    for a in assets:
        remap: dict[str, str] = {}
        for sp in a.speakers:
            nid = f"{a.asset_id}:{sp.id}"
            canonical = merges.get(nid, nid)
            remap[sp.id] = canonical
            existing = out.get(canonical)
            if existing is None:
                out[canonical] = spk.Speaker(
                    id=canonical, source=sp.source,
                    # The reel is part of the name until a human merges them.
                    # Two rows both reading "Speaker 1" is the exact confusion
                    # this whole function exists to prevent.
                    default_label=f"{sp.default_label} · {short[a.asset_id]}",
                    track_index=sp.track_index, word_count=sp.word_count,
                    speech_ms=sp.speech_ms, confirmed=sp.confirmed,
                    label=sp.label)
            else:
                existing.word_count += sp.word_count
                existing.speech_ms += sp.speech_ms
                # A merged speaker is one person across reels; the per-reel
                # suffix is now a lie, so drop it.
                base = existing.default_label.split(" · ")[0]
                existing.default_label = base
                existing.label = existing.label or sp.label
        for b in a.beats:
            b.speaker = remap.get(b.speaker, f"{a.asset_id}:{b.speaker}")

    return sorted(out.values(), key=lambda s: -s.speech_ms)


def parse_merges(specs: list[str]) -> dict[str, str]:
    """`--merge-speakers a:SPK1=b:SPK2` — the second voice becomes the first."""
    out: dict[str, str] = {}
    for spec in specs or []:
        canonical, _, other = spec.partition("=")
        if not other:
            raise ValueError(
                f"--merge-speakers wants canonical=other, got {spec!r}")
        out[other.strip()] = canonical.strip()
    return out
