"""Stage 3 — silence and speech map.

This is the bridge between text-level decisions and cuts that sound natural. The
engine reasons about *what* to keep from the transcript; it cannot know *where*
to cut without the waveform. A cut landing mid-breath sounds wrong no matter how
good the sentence was.

Silero VAD, which ships inside faster-whisper as a bundled ONNX model — no
download, no torch, and it runs offline.
"""

from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


@dataclass
class SpeechMap:
    """Speech intervals in milliseconds, and the gaps between them."""

    speech: list[tuple[int, int]]
    duration_ms: int

    @property
    def silence(self) -> list[tuple[int, int]]:
        out, prev = [], 0
        for start, end in self.speech:
            if start > prev:
                out.append((prev, start))
            prev = end
        if prev < self.duration_ms:
            out.append((prev, self.duration_ms))
        return out

    def nearest_silence(self, ms: int, search_ms: int = 1200) -> int | None:
        """Nearest silence-interval midpoint within `search_ms`.

        Stage 9 snaps cut points outward to these. Returning None means there is
        no silence nearby and the cut has to land inside speech — worth flagging
        rather than doing quietly.
        """
        best, best_d = None, search_ms + 1
        for start, end in self.silence:
            mid = (start + end) // 2
            d = abs(mid - ms)
            if d < best_d:
                best, best_d = mid, d
        return best


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"{path.name} is {w.getframerate()} Hz; VAD needs {SAMPLE_RATE}"
            )
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def build(path: Path, min_silence_ms: int = 250,
          speech_pad_ms: int = 30) -> SpeechMap:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    audio = read_wav(path)
    stamps = get_speech_timestamps(
        audio,
        VadOptions(min_silence_duration_ms=min_silence_ms,
                   speech_pad_ms=speech_pad_ms),
    )
    speech = [
        (int(s["start"] / SAMPLE_RATE * 1000), int(s["end"] / SAMPLE_RATE * 1000))
        for s in stamps
    ]
    return SpeechMap(speech=speech,
                     duration_ms=int(len(audio) / SAMPLE_RATE * 1000))
