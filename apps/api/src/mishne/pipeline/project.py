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
from ..timecode import Rate
from .steps import aaf_ingest, audio as audio_step, prepare, speakers as spk
from .steps import structure, transcribe, vad
from .steps.structure import Beat
from .steps.vad import SpeechMap

# Bump when anything that shapes a cached ingest changes — segmentation rules,
# speaker attribution, what gets stored. A stale cache is worse than a slow
# one: it serves beats built by code that no longer exists, and the only symptom
# is a cut that looks subtly wrong. Transcription is keyed separately and is not
# repaid by a bump here.
CACHE_VERSION = 2


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


def asset_id_for(path: Path) -> str:
    """A stable id for an upload.

    Filename plus size — enough to be stable across runs and to notice when a
    different file arrives under a name already used. A content hash would be
    better and costs a full read of a very large file; revisit if collisions
    ever matter.
    """
    stem = "".join(c if c.isalnum() else "_" for c in path.stem)[:40]
    try:
        return f"{stem}_{path.stat().st_size % 100000:05d}"
    except OSError:
        return stem


def ingest(path: Path, work_dir: Path, language: str | None = None,
           provider: str = "faster-whisper", replay: Path | None = None,
           model: str = "base", model_path: str | None = None,
           assume_rate: Rate | None = None, diarize_models: Path | None = None,
           on_progress=None) -> AssetIngest:
    """Stages 0-4 plus speaker attribution for one asset. Cached on disk."""
    path = Path(path)
    aid = asset_id_for(path)
    adir = work_dir / "assets" / aid
    adir.mkdir(parents=True, exist_ok=True)
    say = on_progress or (lambda *_: None)

    cached = adir / "ingest.json"
    if cached.exists():
        try:
            hit = _load(cached, path, adir)
            if hit is not None:
                return hit
            say("cache written by an older pipeline, re-ingesting")
        except Exception:  # noqa: BLE001 — a stale cache is not worth dying for
            say("cache unreadable, re-ingesting")

    aaf = None
    provenance, seams = "rushes", []
    if path.suffix.lower() == ".aaf":
        aaf = aaf_ingest.parse(path)
        say(f"AAF · {len(aaf.clips)} clips · "
            f"{'embedded' if aaf.embedded else 'linked'}")
        flat = aaf_ingest.flatten_audio(aaf, adir)
        info = prepare.probe(flat, assume_rate=aaf.rate)
        info.start_tc_frames = aaf.start_tc_frames
        info.duration_frames = aaf.duration_frames
        tracks = audio_step.extract(info, adir)
        # A sequence of more than one clip means a person has already made cut
        # decisions in this material. Their positions on the flattened
        # timeline, in ms — the boundary between clips, not clip zero's start.
        if len(aaf.clips) > 1:
            provenance = "sequence"
            seams = [round(c.tl_in / aaf.rate.fps * 1000)
                     for c in aaf.clips[1:]]
    else:
        info = prepare.probe(path, assume_rate=assume_rate)
        tracks = audio_step.extract(info, adir)
    if not tracks:
        raise ValueError(f"{path.name} has no audio")

    speech = vad.build(tracks[0].path)
    say(f"{len(speech.speech)} speech segments")

    kwargs = ({"path": replay} if replay
              else {"model": model, "model_path": model_path})
    asr: ASRResult = transcribe.run(
        tracks[0].path, adir,
        provider="replay" if replay else provider,
        language=language, **kwargs)
    say(f"{len(asr.words)} words · {asr.language}")

    # Multi-track material needs no model: the loudest microphone is whoever is
    # talking. One track needs diarization, and without it the honest answer is
    # that the voices were never separated — not a confident "Speaker 1".
    if len(tracks) > 1:
        attribution = spk.attribute_from_files(
            asr.words, {t.track_index: t.path for t in tracks})
    elif diarize_models:
        from ..diarize import get_provider
        say("separating voices")
        # The source clips are what keeps the clustering about people rather
        # than microphones — see diarize/sherpa_provider.py.
        regions = ([(s0, s1) for s0, s1 in zip([0] + seams, seams
                    + [round(info.duration_frames / info.rate.fps * 1000)])]
                   if seams else None)
        dia = get_provider("sherpa-onnx", model_dir=Path(diarize_models)
                           ).diarize(tracks[0].path, regions=regions)
        attribution = spk.attribute_from_diarization(asr.words, dia)
        say(f"{len(attribution.speakers)} voice(s)"
            + ("" if attribution.reliable else " — separation is weak"))
    else:
        attribution = spk.single_track_unseparated(asr.words)

    beats = structure.build(asr.words, speech, language=asr.language,
                            loudness_lufs=tracks[0].integrated_lufs,
                            asset_id=aid, seams=seams)
    warnings = list(getattr(structure.build, "warnings", []))
    say(f"{len(beats)} beats")

    result = AssetIngest(
        asset_id=aid, path=path, rate=info.rate,
        start_tc_frames=info.start_tc_frames,
        duration_frames=info.duration_frames, language=asr.language,
        beats=beats, speakers=attribution.speakers, attribution=attribution,
        speech=speech, audio_path=tracks[0].path, aaf=aaf,
        audio_tracks=len(tracks), provenance=provenance, seams=seams,
        warnings=warnings,
    )
    _save(result, cached)
    return result


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
        "seams": a.seams,
        "warnings": a.warnings,
        "speakers": [s.to_dict() for s in a.speakers],
        "beats": [{
            "id": b.id, "idx": b.idx, "assetId": b.asset_id,
            "speaker": b.speaker, "startMs": b.start_ms, "endMs": b.end_ms,
            "text": b.text, "flags": b.flags,
            "confidence": round(b.mean_confidence, 3),
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
                  mean_confidence=b.get("confidence", 1.0))
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
