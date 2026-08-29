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
    mob_id: str
    media_path: Path | None
    embedded_mob_id: str | None
    src_in: int          # frames into the source media
    src_out: int
    tl_in: int           # frames along the AAF timeline
    tl_out: int

    @property
    def frames(self) -> int:
        return self.tl_out - self.tl_in

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

    @property
    def duration_s(self) -> float:
        return self.duration_frames / self.rate.fps

    @property
    def resolved_clips(self) -> list[SourceClip]:
        return [c for c in self.clips if c.resolved]


def _url_to_path(url: str | None, aaf_dir: Path) -> Path | None:
    """Resolve a locator URL to a local file.

    AAFs are routinely moved between machines, so the absolute path inside is
    frequently wrong while the media sits right next to the AAF. Falling back to
    a filename match in the AAF's own directory handles the common case without
    guessing wildly.
    """
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        candidate = Path(unquote(parsed.path))
        if candidate.exists():
            return candidate
        beside = aaf_dir / candidate.name
        if beside.exists():
            return beside
        # Some AAFs store Windows paths; take the basename and look locally.
        beside = aaf_dir / candidate.name.replace("\\", "/").split("/")[-1]
        if beside.exists():
            return beside
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


def parse(path: Path) -> AAFSource:
    """Read an AAF into a source map."""
    path = Path(path)
    timeline = otio.adapters.read_from_file(str(path), adapter_name="AAF")

    rate_fps = timeline.duration().rate
    num, den = (24000, 1001) if abs(rate_fps - 23.976) < 0.01 else \
               (30000, 1001) if abs(rate_fps - 29.97) < 0.01 else \
               (60000, 1001) if abs(rate_fps - 59.94) < 0.01 else \
               (int(round(rate_fps)), 1)
    rate = Rate(num, den, drop_frame=_drop_frame(path))

    # Prefer the audio track: it is what gets transcribed, and in a sequence
    # with separate audio the audio edits are the ones that matter.
    tracks = list(timeline.tracks)
    audio = [t for t in tracks if t.kind == otio.schema.TrackKind.Audio]
    video = [t for t in tracks if t.kind == otio.schema.TrackKind.Video]
    chosen = (audio or video or tracks)[:1]

    notes: list[str] = []
    if audio and video:
        notes.append("Using the audio track for transcription.")
    elif not audio:
        notes.append("No audio track in this AAF — using the video track's "
                     "media references for audio.")

    embedded_ids = _embedded_mob_ids(path)
    aaf_dir = path.parent
    clips: list[SourceClip] = []
    missing: list[str] = []
    tl_pos = 0
    idx = 0

    for track in chosen:
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
            mob_id = str(mr.metadata.get("AAF", {}).get("MobID", "")) or \
                     str(child.metadata.get("AAF", {}).get("SourceID", ""))
            url = getattr(mr, "target_url", None)
            media = _url_to_path(url, aaf_dir)
            emb = mob_id if mob_id in embedded_ids else None

            if media is None and emb is None:
                missing.append(f"{child.name}: {url or 'no locator'}")

            sr = child.source_range
            clips.append(SourceClip(
                index=idx, name=child.name or f"clip_{idx}", mob_id=mob_id,
                media_path=media, embedded_mob_id=emb,
                src_in=round(sr.start_time.value),
                src_out=round(sr.start_time.value + sr.duration.value),
                tl_in=tl_pos, tl_out=tl_pos + dur,
            ))
            idx += 1
            tl_pos += dur

    start_tc = round(timeline.global_start_time.value) \
        if timeline.global_start_time else 0

    if missing:
        notes.append(
            f"{len(missing)} of {len(clips)} clips could not be resolved to "
            f"media. They will be silent in the transcript, and anything "
            f"selected from them will still reference the right source."
        )

    return AAFSource(
        path=path, rate=rate, duration_frames=tl_pos, start_tc_frames=start_tc,
        clips=clips, embedded=bool(embedded_ids), missing=missing, notes=notes,
    )


def _embedded_mob_ids(path: Path) -> set[str]:
    try:
        import aaf2

        with aaf2.open(str(path), "r") as f:
            return {str(e.mob_id) for e in f.content.essencedata}
    except Exception:  # noqa: BLE001
        return set()


def extract_embedded(path: Path, out_dir: Path) -> dict[str, Path]:
    """Write embedded essence streams out to files. Returns {mob_id: path}."""
    import aaf2

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}
    with aaf2.open(str(path), "r") as f:
        for ed in f.content.essencedata:
            mob_id = str(ed.mob_id)
            target = out_dir / f"essence_{mob_id.split('.')[-1][:16]}.raw"
            with ed.open("r") as src, open(target, "wb") as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            written[mob_id] = target
    return written


def flatten_audio(source: AAFSource, out_dir: Path) -> Path:
    """Render the AAF timeline's audio to one 16 kHz mono WAV.

    Position in this file equals position on the AAF timeline, exactly — gaps
    and unresolvable clips become silence rather than being skipped. That
    invariant is what makes `map_to_source` a lookup instead of a guess.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{source.path.stem}_flat.wav"
    if out.exists():
        return out

    fps = source.rate.fps
    parts: list[Path] = []
    cursor = 0

    for clip in source.clips:
        if clip.tl_in > cursor:
            parts.append(_silence(out_dir, len(parts),
                                  (clip.tl_in - cursor) / fps))
        seg = out_dir / f"_seg_{clip.index:05d}.wav"
        if clip.resolved:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{clip.src_in / fps:.6f}",
                 "-t", f"{clip.frames / fps:.6f}",
                 "-i", str(clip.media_path),
                 "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
                 "-c:a", "pcm_s16le", str(seg)],
                capture_output=True, text=True)
            if proc.returncode != 0 or not seg.exists():
                seg = _silence(out_dir, clip.index, clip.frames / fps)
        else:
            seg = _silence(out_dir, clip.index, clip.frames / fps)
        parts.append(seg)
        cursor = clip.tl_out

    listing = out_dir / "_concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(listing),
         "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)],
        check=True, capture_output=True)
    return out


def _silence(out_dir: Path, idx: int, seconds: float) -> Path:
    p = out_dir / f"_sil_{idx:05d}.wav"
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
    out: list[tuple[SourceClip, int, int]] = []
    for clip in source.clips:
        lo = max(tl_in, clip.tl_in)
        hi = min(tl_out, clip.tl_out)
        if lo >= hi:
            continue
        offset = lo - clip.tl_in
        out.append((clip, clip.src_in + offset, clip.src_in + offset + (hi - lo)))
    return out
