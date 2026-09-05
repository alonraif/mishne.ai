"""AAF ingest — stage 0 for sequences.

ffprobe cannot read an AAF. It is OLE structured storage, not a media
container, so the flat-file path in `prepare.py` fails on it outright. This
module is the `aaf_embedded` ingest mode the architecture describes.

## What an AAF actually is here

A *sequence*, not a file: an ordered list of clips, each pointing at a region of
some source media by timecode. So the model is:

    AAF timeline  →  flattened audio  →  transcript  →  selection
                                                            ↓
    selected timeline ranges  →  mapped back to source clips + mob IDs

**The AAF's timeline is the source.** If an editor hands over a selects
sequence, the job is to cut that down, and the output must reference the same
originals they already have in their bin.

## Why this is the best input we can get

The AAF carries its own **source mob IDs**. Inheriting them means the rough cut
relinks *silently* in the project it came from — no relink dialog, no locating
files. Spike A established that the MobID is the relink key; this is where we get
real ones instead of synthesising them.

## Units are not uniform, and this will catch you

A real AAF mixes rates *within one clip object*. In a 25 fps sequence with
48 kHz production audio, OTIO reports:

    source_range.start_time   in SAMPLES at 48000
    source_range.duration     in FRAMES  at 25

Start comes from the source mob (sample rate); duration comes from the sequence
component (edit rate). Treating either as the other silently scales every cut by
1920x. Each clip therefore carries its own `src_rate`, and conversions go
through seconds rather than assuming a shared unit.

## Source position is timecode, not a file offset

The start OTIO reports is a position in the *source's timecode space* — a field
recorder's running clock, often tens of thousands of seconds. The essence file
begins at that mob's own `StartTime`, so:

    offset into the file  =  reported start  −  mob StartTime

Skip that subtraction and every seek lands far past the end of a 35-second WAV,
ffmpeg returns nothing, and the flattened audio comes out the right length and
completely silent. Which is exactly what happened the first time.

Both coordinates are kept. The file offset is for reading audio; the timecode
position is what the output references, and it is what makes the result relink.

## Two resolution modes

- **Linked** — clips reference external files. Fast, but the files must be
  present and reachable at the paths the AAF names.
- **Embedded** — the essence lives inside the AAF. Self-contained, and usually
  enormous. Extracted to a working directory.

Both are handled. Unresolvable references are collected and reported rather than
skipped: a sequence that silently transcribes 60% of itself is worse than one
that refuses.
"""

from __future__ import annotations

import math
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

import opentimelineio as otio

from ...timecode import Rate

SAMPLE_RATE = 16000


@dataclass
class SourceClip:
    """One clip on the AAF timeline, and where its media actually is."""

    index: int
    name: str
    #: The **MasterMob** id. This is the relink key: what Avid matches a clip
    #: in an AAF to media in a bin by, and what `assemble` writes into the
    #: output so a cut opens against the customer's own rushes (Spike A).
    mob_id: str
    #: The **SourceMob** id behind that master, which is a different id and is
    #: not interchangeable with it. It is the key `_mob_origins` is built on,
    #: because `StartTime` is a property of the source mob rather than the
    #: master. Kept as its own field because the two were one field for a
    #: while: whichever id was stored, one of the two jobs silently got the
    #: wrong one — either the output relinked to nothing, or every clip lost
    #: its timecode origin and the flattened mix came out short by the total of
    #: the consolidation handles.
    source_mob_id: str
    media_path: Path | None
    embedded_mob_id: str | None
    # Source position in the SOURCE's own units — samples for production audio,
    # frames for picture. Never assume these are timeline frames.
    src_in: int
    src_out: int
    src_rate: float
    # The mob's own timecode origin. Subtract it from src_in to get a byte
    # position in the essence; keep src_in itself for output references.
    origin: int
    tl_in: int           # frames along the AAF timeline
    tl_out: int
    #: The locator as the AAF spells it, kept whether or not it resolved. When
    #: it did not, this is the only description of the file the customer has to
    #: upload, and B2's `asset_media_requirements` is built from it. Defaulted
    #: because it is additive: every existing construction call still works.
    target_url: str | None = None
    #: Which of the sequence's sound tracks this clip sits on. A podcast AAF
    #: keeps each microphone on its own track, and the media those tracks
    #: reference has to be asked for in full even though only one of them is
    #: the track the cut is expressed against (ADR-0019).
    track_index: int = 0
    track_name: str | None = None

    @property
    def frames(self) -> int:
        """Length on the timeline, in timeline frames."""
        return self.tl_out - self.tl_in

    @property
    def src_in_seconds(self) -> float:
        """Seconds into the essence FILE — origin removed."""
        if not self.src_rate:
            return 0.0
        return max(0.0, (self.src_in - self.origin) / self.src_rate)

    @property
    def resolved(self) -> bool:
        return self.media_path is not None and self.media_path.exists()


@dataclass
class AAFSource:
    path: Path
    rate: Rate
    duration_frames: int
    start_tc_frames: int
    clips: list[SourceClip]
    embedded: bool = False
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    #: The track the cut is expressed against. `clips` spans every sound track
    #: — that is what the transcript is mixed from and what the customer is
    #: asked to upload — but a timeline range maps back to source clips on one
    #: track, because that is what the output document can say (ADR-0019).
    primary_track: int = 0

    #: Whether the sequence has a picture track at all.
    #:
    #: Recorded here because the parse already knows — it looks for video in
    #: order to prefer audio over it — and nothing downstream can recover it:
    #: `clips` holds only the chosen tracks, and `width`/`height` describe a
    #: file rather than a sequence. Stage 10 needs the answer to decide whether
    #: to build a V1 track, and Media Composer refuses an entire sequence whose
    #: V1 clip resolves to a source with no picture.
    has_video: bool = False

    @property
    def duration_s(self) -> float:
        return self.duration_frames / self.rate.fps

    @property
    def primary_clips(self) -> list[SourceClip]:
        """The clips the output references, in timeline order."""
        return [c for c in self.clips if c.track_index == self.primary_track]

    @property
    def tracks(self) -> list[int]:
        return sorted({c.track_index for c in self.clips})


def basename_of(url: str | None) -> str:
    """The filename an AAF locator points at, whatever shape the locator is in.

    Locators in the wild are `file:///Volumes/...`, bare Windows paths with
    backslashes, percent-encoded URLs, and occasionally nothing at all. All that
    is wanted is the last segment.

    It lives here rather than in `db.requirements`, which is where it started
    and which still re-exports it, because two callers now need it and the
    other one is stage 10: an AAF's own locator is the only place the
    *customer's* name for a linked source survives. The pipeline does not import
    from `db`, and this is a pure string function about AAF locators, so this is
    where it belongs.
    """
    if not url:
        return ""
    raw = unquote(urlparse(url).path or url) if "://" in url else unquote(url)
    return raw.replace("\\", "/").rstrip("/").split("/")[-1]


def search_dirs_for(path: Path, extra: list[Path] | None = None) -> list[Path]:
    """Where to look for media the locator's own absolute path does not find.

    The AAF's own directory first — that is where a worker materialises the
    companions, and ADR-0014 rests on it — then the folder Media Composer
    exports alongside the AAF, then any other directory one level down, then
    whatever the caller named.

    `AAF Media/` is not an exotic case; it is what "export AAF with linked
    media" produces, next to an absolute path into a filesystem we will never
    see. Only immediate children are considered: a recursive walk of somebody's
    media drive is a different and much worse promise.
    """
    aaf_dir = path.parent
    dirs = [aaf_dir]
    conventional = aaf_dir / "AAF Media"
    if conventional.is_dir():
        dirs.append(conventional)
    try:
        for child in sorted(aaf_dir.iterdir()):
            if child.is_dir() and child not in dirs:
                dirs.append(child)
    except OSError:  # an unreadable directory is not an error here
        pass
    for named in extra or []:
        named = Path(named)
        if named.is_dir() and named not in dirs:
            dirs.append(named)
    return dirs


def _url_to_path(url: str | None,
                 search_dirs: Path | list[Path]) -> Path | None:
    """Resolve a locator URL to a local file.

    AAFs are routinely moved between machines, so the absolute path inside is
    frequently wrong while the media sits right next to the AAF — or one level
    down, in the folder the locator itself names. Two fallbacks per directory:
    the bare basename, and the last two segments of the locator's path, which
    is what finds `AAF Media/A001.wav` under a directory that is no longer
    called what the exporting machine called it.
    """
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("", "file"):
        return None
    raw = unquote(parsed.path or url)

    # As written. Correct on the machine that exported the AAF, and there only.
    candidate = Path(raw)
    if candidate.exists():
        return candidate

    # Some AAFs store Windows paths, backslashes and all.
    segments = [seg for seg in raw.replace("\\", "/").rstrip("/").split("/") if seg]
    if not segments:
        return None
    name = segments[-1]
    tail = "/".join(segments[-2:])

    dirs = [search_dirs] if isinstance(search_dirs, Path) else list(search_dirs)
    for directory in dirs:
        beside = directory / name
        if beside.exists():
            return beside
        nested = directory / tail
        if nested.exists():
            return nested
    return None


def _drop_frame(path: Path) -> bool:
    """Read the drop-frame flag from the AAF's timecode component."""
    try:
        import aaf2

        with aaf2.open(str(path), "r") as f:
            for mob in f.content.mobs:
                for slot in mob.slots:
                    seg = getattr(slot, "segment", None)
                    if seg is not None and type(seg).__name__ == "Timecode":
                        return bool(seg["Drop"].value)
    except Exception:  # noqa: BLE001 — absence is a legitimate answer
        pass
    return False


def parse(path: Path, search_dirs: list[Path] | None = None) -> AAFSource:
    """Read an AAF into a source map.

    `search_dirs` names extra directories to look for linked media in. The
    AAF's own directory and the folders one level down are always searched;
    this is for a caller who keeps the media somewhere else entirely.
    """
    path = Path(path)
    timeline = otio.adapters.read_from_file(str(path), adapter_name="AAF")

    rate_fps = timeline.duration().rate
    num, den = (24000, 1001) if abs(rate_fps - 23.976) < 0.01 else \
               (30000, 1001) if abs(rate_fps - 29.97) < 0.01 else \
               (60000, 1001) if abs(rate_fps - 59.94) < 0.01 else \
               (int(round(rate_fps)), 1)
    rate = Rate(num, den, drop_frame=_drop_frame(path))

    # Prefer the audio tracks: they are what gets transcribed, and in a sequence
    # with separate audio the audio edits are the ones that matter.
    #
    # EVERY sound track, not the first. A four-microphone podcast keeps each mic
    # on its own track; taking one of them asks the customer to upload a quarter
    # of the media and then transcribes a quarter of the room. The tracks are
    # mixed for transcription and the cut is expressed against the first of them
    # (ADR-0019).
    tracks = list(timeline.tracks)
    audio = [t for t in tracks if t.kind == otio.schema.TrackKind.Audio]
    video = [t for t in tracks if t.kind == otio.schema.TrackKind.Video]
    chosen = audio or video or tracks

    notes: list[str] = []
    if audio and video:
        notes.append("Using the audio for transcription.")
    elif not audio:
        notes.append("No audio track in this AAF — using the video track's "
                     "media references for audio.")
    if len(chosen) > 1:
        notes.append(f"{len(chosen)} sound tracks — mixed for transcription.")

    embedded_ids = _embedded_mob_ids(path)
    origins = _mob_origins(path)
    dirs = search_dirs_for(path, search_dirs)
    clips: list[SourceClip] = []
    missing: list[str] = []
    duration_frames = 0
    idx = 0

    for track_index, track in enumerate(chosen):
        # Tracks are parallel, so each one's position starts again at zero.
        tl_pos = 0
        for child in track:
            dur = round(child.duration().value)
            if isinstance(child, otio.schema.Gap):
                # Gaps are kept as timeline position so the flattened audio and
                # the AAF timeline stay in step. Silence is inserted later.
                tl_pos += dur
                continue
            if not isinstance(child, otio.schema.Clip):
                tl_pos += dur
                continue

            mr = child.media_reference
            # The relink key, and the order matters as much as the lookup.
            #
            # Avid matches a clip to media in a bin by the **MasterMob** id
            # (Spike A). Getting this wrong does not fail: it produces an AAF
            # that opens, shows the right timecodes and clip names, and cannot
            # find a single frame of media — which is exactly what a real Media
            # Composer export did.
            #
            # `clip.metadata["AAF"]["MobID"]` is the MasterMob. Measured over
            # two real sequences — a Media Composer export and a 775-clip
            # multitrack sync — it is the MasterMob for every clip in both.
            #
            # `media_reference.metadata["AAF"]["MobID"]` is *not* the same id.
            # Where it is populated it is the SourceMob behind the master, and
            # it matched no MasterMob in either file. It was being read first,
            # so a sequence that had one relinked to nothing, and a sequence
            # that did not — the export above — fell through to a synthesised
            # id and also relinked to nothing. It is kept only as a fallback for
            # a sequence that carries no clip-level id at all.
            #
            # `SourceID` is not a key the OTIO adapter writes; it stays last and
            # costs nothing.
            aaf_meta = child.metadata.get("AAF", {})
            master_id = str(aaf_meta.get("MobID", ""))
            source_id = str(mr.metadata.get("AAF", {}).get("MobID", ""))
            # The relink key. Falls back to the source mob only when there is no
            # master to name, which is better than naming nothing.
            mob_id = master_id or source_id or str(aaf_meta.get("SourceID", ""))
            url = getattr(mr, "target_url", None)
            media = _url_to_path(url, dirs)
            # Embedded essence is stored against the SOURCE mob, like the
            # origins above and unlike the relink key. Matching the master here
            # finds nothing in a self-contained AAF, and every clip in it then
            # looks unresolved — the sequence flattens to silence and the
            # transcript comes back empty, with no error anywhere.
            emb = next(
                (i for i in (source_id, mob_id) if i and i in embedded_ids),
                None,
            )

            if media is None and emb is None:
                missing.append(f"{child.name}: {url or 'no locator'}")

            sr = child.source_range
            # start_time and duration can carry DIFFERENT rates on the same
            # object. Take each from its own.
            src_rate = float(sr.start_time.rate)
            src_start = round(sr.start_time.value)
            # Express the clip's source extent in source units, derived from the
            # timeline duration in seconds rather than from duration.value —
            # which is in edit-rate frames, not source units.
            src_len = round(dur / rate_fps * src_rate)

            clips.append(SourceClip(
                index=idx, name=child.name or f"clip_{idx}", mob_id=mob_id,
                source_mob_id=source_id,
                media_path=media, embedded_mob_id=emb,
                src_in=src_start, src_out=src_start + src_len,
                # Keyed on the SOURCE mob: `StartTime` belongs to the source
                # mob, not the master, so looking this up by the relink key
                # finds nothing and every clip silently gets an origin of 0 —
                # which reads the essence from the wrong offset and shortens the
                # flattened mix by the consolidation handle on every clip.
                src_rate=src_rate,
                origin=origins.get(source_id, origins.get(mob_id, 0)),
                tl_in=tl_pos, tl_out=tl_pos + dur, target_url=url,
                track_index=track_index, track_name=track.name or None,
            ))
            idx += 1
            tl_pos += dur
        duration_frames = max(duration_frames, tl_pos)

    start_tc = round(timeline.global_start_time.value) \
        if timeline.global_start_time else 0

    if embedded_ids:
        notes.append(f"{len(embedded_ids)} embedded essence stream(s) — "
                     f"self-contained AAF, no external media needed.")
    if missing:
        notes.append(
            f"{len(missing)} of {len(clips)} clips could not be resolved to "
            f"media. They will be silent in the transcript, and anything "
            f"selected from them will still reference the right source."
        )

    return AAFSource(
        path=path, rate=rate, duration_frames=duration_frames,
        start_tc_frames=start_tc, clips=clips, embedded=bool(embedded_ids),
        missing=missing, notes=notes, primary_track=0,
        has_video=bool(video),
    )


def _mob_origins(path: Path) -> dict[str, int]:
    """Each source mob's timecode origin, keyed by mob id.

    This is the value that turns a timecode position into a file offset. In this
    material every clip sat exactly 576000 samples (12 s) past its origin —
    Avid's standard consolidation handle.
    """
    origins: dict[str, int] = {}
    try:
        import aaf2

        f = aaf2.open(str(path), "r")
        try:
            for sm in f.content.sourcemobs():
                for slot in sm.slots:
                    seg = getattr(slot, "segment", None)
                    if seg is not None and hasattr(seg, "keys") \
                            and "StartTime" in seg:
                        origins[str(sm.mob_id)] = int(seg["StartTime"].value)
        finally:
            f.close()
    except Exception:  # noqa: BLE001
        pass
    return origins


def _embedded_mob_ids(path: Path) -> set[str]:
    try:
        import aaf2

        with aaf2.open(str(path), "r") as f:
            return {str(e.mob_id) for e in f.content.essencedata}
    except Exception:  # noqa: BLE001
        return set()


def extract_embedded(path: Path, out_dir: Path) -> dict[str, Path]:
    """Write embedded essence streams out as files. Returns {mob_id: path}.

    For WAVE essence the stream is already a complete RIFF file, header
    included, so this is a straight copy rather than a rebuild.
    """
    import aaf2

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    f = aaf2.open(str(path), "r")
    try:
        for ed in f.content.essencedata:
            mob_id = str(ed.mob_id)
            stem = mob_id.replace(":", "_").replace(".", "_")[-20:]
            stream = ed.open("r")
            head = bytes(stream.read(12))
            ext = ".wav" if head[:4] == b"RIFF" else ".raw"
            target = out_dir / f"essence_{stem}{ext}"
            if target.exists() and target.stat().st_size > 1024:
                written[mob_id] = target
                continue
            with open(target, "wb") as dst:
                dst.write(head)
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
            written[mob_id] = target
    finally:
        f.close()
    return written


def _render_track(source: AAFSource, clips: list[SourceClip], out_dir: Path,
                  essence: dict[str, Path], tag: str, target: Path) -> Path:
    """Render one track's clips to `target`, timeline position preserved."""
    fps = source.rate.fps
    parts: list[Path] = []
    cursor = 0
    gaps = 0

    for clip in clips:
        if clip.tl_in > cursor:
            gaps += 1
            parts.append(_silence(out_dir, f"{tag}_g{gaps:05d}",
                                  (clip.tl_in - cursor) / fps))
        seg = out_dir / f"_seg_{clip.index:05d}.wav"
        media = clip.media_path
        if media is None and clip.embedded_mob_id:
            media = essence.get(clip.embedded_mob_id)

        if media is not None and media.exists():
            # Seek in SECONDS, computed from the clip's own source rate. Using
            # the timeline fps here would be wrong by the ratio between them.
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{clip.src_in_seconds:.6f}",
                 "-t", f"{clip.frames / fps:.6f}",
                 "-i", str(media),
                 "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                 "-c:a", "pcm_s16le", str(seg)],
                capture_output=True, text=True)
            if proc.returncode != 0 or not seg.exists() or seg.stat().st_size < 100:
                seg = _silence(out_dir, f"{tag}_c{clip.index:05d}",
                               clip.frames / fps)
        else:
            seg = _silence(out_dir, f"{tag}_c{clip.index:05d}",
                           clip.frames / fps)
        parts.append(seg)
        cursor = clip.tl_out

    if not parts:
        # A track of nothing but gaps still has to be the sequence's length, or
        # the mix is shorter than the timeline it describes.
        parts.append(_silence(out_dir, f"{tag}_empty",
                              max(source.duration_frames, 1) / fps))

    listing = out_dir / f"_concat_{tag}.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(listing),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(target)],
        check=True, capture_output=True)
    return target


def track_render_name(track_index: int) -> str:
    return f"_track_{track_index:02d}.wav"


def track_renders(source: AAFSource, out_dir: Path) -> dict[int, Path]:
    """The per-track WAVs `flatten_audio` left behind, if there are any.

    They are one microphone each, at the sequence's full length, aligned to the
    same timeline as the mix — which is exactly the input
    `speakers.attribute_from_files` wants. A four-microphone podcast therefore
    gets its speakers by arithmetic, the way multi-track material always has:
    the loudest mic is whoever is talking. Mixing for transcription (ADR-0019)
    must not cost that, and without this it would.

    Deterministic names checked for existence, rather than a second return
    value threaded through every caller of `flatten_audio`.
    """
    if len(source.tracks) < 2:
        return {}
    found = {}
    for track in source.tracks:
        path = out_dir / track_render_name(track)
        if path.exists():
            found[track] = path
    return found


def flatten_audio(source: AAFSource, out_dir: Path) -> Path:
    """Render the AAF timeline's audio to one 16 kHz mono WAV.

    Position in this file equals position on the AAF timeline, exactly — gaps
    and unresolvable clips become silence rather than being skipped. That
    invariant is what makes `map_to_source` a lookup instead of a guess.

    A sequence with several sound tracks — four microphones on four tracks is
    what a podcast AAF looks like — is rendered track by track and mixed
    (ADR-0019). One track takes the path it always took and produces the same
    bytes: there is no mix where there is nothing to mix.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{source.path.stem}_flat.wav"
    if out.exists():
        return out

    # Embedded essence has to come out of the container before ffmpeg can read
    # it. Done once, cached on disk, because a long sequence references the
    # same streams many times.
    essence: dict[str, Path] = {}
    if source.embedded:
        essence = extract_embedded(source.path, out_dir / "essence")

    tracks = source.tracks or [source.primary_track]
    if len(tracks) == 1:
        return _render_track(source, source.clips, out_dir, essence,
                             f"{tracks[0]:02d}", out)

    rendered = [
        _render_track(source, [c for c in source.clips if c.track_index == t],
                      out_dir, essence, f"{t:02d}",
                      out_dir / track_render_name(t))
        for t in tracks
    ]

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for part in rendered:
        cmd += ["-i", str(part)]
    # Sum, not average. `normalize=1` divides by the input count, so four tracks
    # where one person is talking make that person a quarter as loud — and a
    # quiet mic is the case transcription is already worst at. The 1/sqrt(N)
    # trim is the incoherent-sum compensation: it keeps four mics off the
    # clipping ceiling without flattening one.
    trim = 1.0 / math.sqrt(len(rendered))
    cmd += ["-filter_complex",
            (f"amix=inputs={len(rendered)}:duration=longest:"
             f"dropout_transition=0:normalize=0,volume={trim:.6f}"),
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _silence(out_dir: Path, tag: str, seconds: float) -> Path:
    """A silent WAV of exactly this length.

    `tag` has to be unique within a render. It used to be an int taken from two
    different counters — a clip index for an unresolvable clip and a
    parts-length for a gap — so a gap and a clip could name the same file, and
    the second write silently changed the first one's length.
    """
    p = out_dir / f"_sil_{tag}.wav"
    frames = max(1, int(seconds * SAMPLE_RATE))
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * frames)
    return p


def map_to_source(source: AAFSource, tl_in: int,
                  tl_out: int) -> list[tuple[SourceClip, int, int]]:
    """Map a timeline range back to source clips.

    Returns (clip, source_in, source_out) per overlapping clip. A selection that
    spans a cut in the original sequence yields **more than one** entry, and
    becomes more than one clip in the output — which is correct: those frames
    genuinely come from different sources.
    """
    fps = source.rate.fps
    out: list[tuple[SourceClip, int, int]] = []
    for clip in source.primary_clips:
        lo = max(tl_in, clip.tl_in)
        hi = min(tl_out, clip.tl_out)
        if lo >= hi:
            continue
        # Timeline frames -> seconds -> the source's own units. Going straight
        # from frames to source units is the 1920x error.
        offset_units = round((lo - clip.tl_in) / fps * clip.src_rate)
        length_units = round((hi - lo) / fps * clip.src_rate)
        src_in = clip.src_in + offset_units
        out.append((clip, src_in, src_in + length_units))
    return out
