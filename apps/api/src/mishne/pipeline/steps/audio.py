"""Stage 1 — extract audio.

16 kHz mono PCM, **per source track, never on a flattened mix.**

The per-track rule is not fussiness. On a multicam or multi-track shoot, knowing
which microphone a word came from is what lets the engine prefer the
best-recorded take of a line and keep A-roll on the right source. Mix first and
that information is gone for good.

Also measures per-track loudness. Stage 4 uses it to flag off-mic material, and
it is the tiebreak between two deliveries of the same line.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .prepare import MediaInfo


@dataclass
class ExtractedAudio:
    path: Path
    track_index: int
    integrated_lufs: float
    peak_dbfs: float


def extract(info: MediaInfo, out_dir: Path,
            sample_rate: int = 16000) -> list[ExtractedAudio]:
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[ExtractedAudio] = []

    for track in info.audio or []:
        out = out_dir / f"{info.path.stem}_a{track.index}.wav"
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(info.path),
             "-map", f"0:{track.index}",
             "-vn", "-ac", "1", "-ar", str(sample_rate),
             "-c:a", "pcm_s16le", str(out)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"audio extraction failed for track {track.index}: "
                f"{proc.stderr.strip()}"
            )
        lufs, peak = measure_loudness(out)
        results.append(ExtractedAudio(out, track.index, lufs, peak))

    return results


def measure_loudness(path: Path) -> tuple[float, float]:
    """EBU R128 integrated loudness and true peak, via ffmpeg's ebur128."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    text = proc.stderr
    lufs = _last_float(text, r"I:\s*(-?\d+\.?\d*)\s*LUFS")
    peak = _last_float(text, r"Peak:\s*(-?\d+\.?\d*)\s*dBFS")
    return (lufs if lufs is not None else -70.0,
            peak if peak is not None else -70.0)


def _last_float(text: str, pattern: str) -> float | None:
    hits = re.findall(pattern, text)
    return float(hits[-1]) if hits else None
