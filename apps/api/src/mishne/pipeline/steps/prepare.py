"""Stage 0 — probe and normalize.

Establish the time base before anything else. Every timing bug downstream traces
back to getting this wrong, and it surfaces three steps away as "the audio
drifts about a second by the end".

Frame rate is captured as a rational. ffprobe gives `r_frame_rate` as `24000/1001`
already; parsing that to a float and back is exactly how 23.976 becomes 23.98
becomes wrong.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ...timecode import Rate, parse_tc


@dataclass
class AudioTrack:
    index: int
    channels: int
    sample_rate: int
    codec: str


@dataclass
class MediaInfo:
    path: Path
    rate: Rate
    duration_frames: int
    start_tc_frames: int
    start_tc: str
    codec: str
    width: int = 0
    height: int = 0
    audio: list[AudioTrack] = field(default_factory=list)
    has_video: bool = True

    @property
    def duration_s(self) -> float:
        return self.duration_frames / self.rate.fps


def _ffprobe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def probe(path: Path, assume_rate: Rate | None = None) -> MediaInfo:
    path = Path(path)
    data = _ffprobe(path)
    streams = data.get("streams", [])

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    if video is not None:
        num, den = (int(x) for x in video["r_frame_rate"].split("/"))
    elif assume_rate is not None:
        num, den = assume_rate.num, assume_rate.den
    else:
        # Audio-only ingest. There is no frame rate in the file, and guessing
        # one silently is how a cut ends up a frame out everywhere. The caller
        # must say. See docs/adr/0005-audio-only-ingest-path.md.
        raise ValueError(
            f"{path.name} has no video stream, so it carries no frame rate. "
            "Pass the sequence rate explicitly (assume_rate)."
        )

    # Drop-frame is signalled by a ';' in the timecode, not by the rate.
    tc = (
        (video or {}).get("tags", {}).get("timecode")
        or data.get("format", {}).get("tags", {}).get("timecode")
        or "00:00:00:00"
    )
    rate = Rate(num, den, drop_frame=";" in tc)

    duration_s = float(data.get("format", {}).get("duration", 0.0))
    if video is not None and video.get("nb_frames"):
        duration_frames = int(video["nb_frames"])
    else:
        duration_frames = round(duration_s * rate.fps)

    return MediaInfo(
        path=path,
        rate=rate,
        duration_frames=duration_frames,
        start_tc_frames=parse_tc(tc, rate),
        start_tc=tc,
        codec=(video or audios[0] if audios else {}).get("codec_name", "unknown"),
        width=int((video or {}).get("width", 0) or 0),
        height=int((video or {}).get("height", 0) or 0),
        has_video=video is not None,
        audio=[
            AudioTrack(
                index=s["index"],
                channels=int(s.get("channels", 1)),
                sample_rate=int(s.get("sample_rate", 48000)),
                codec=s.get("codec_name", "unknown"),
            )
            for s in audios
        ],
    )
