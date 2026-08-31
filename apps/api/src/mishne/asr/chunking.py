"""Splitting audio that is longer than an engine will take in one request.

Gemini transcribes an hour per request, or half an hour with word timestamps and
diarization on — and this pipeline always has both on, so half an hour is the
real limit. A 90-minute interview is normal material. Something has to split it.

## Split on silence, never on a fixed clock

A hard cut at exactly 1800.000s lands mid-word about as often as not, and a word
cut in half is transcribed as two wrong words with two wrong timestamps — one at
the end of chunk N and one at the start of chunk N+1. Every downstream stage
then sees a beat boundary that is not one.

So: `ffmpeg silencedetect` finds where nobody is talking, and the split goes at
the middle of the last silence before the limit. The VAD stage already owns
silence for the pipeline proper (stage 3), but it runs *after* transcription and
needs the transcript — so this is deliberately its own, cruder pass over the
audio rather than a dependency on it.

## The seam is still a seam

Two things do not survive a split and are honest about it:

**Speaker ids are per request.** `spk_1` in chunk 2 is not `spk_1` in chunk 1 —
the diarizer never heard them together. Labels are namespaced per chunk
(`c1:spk_1`) so nothing downstream can silently merge two different people; the
job of stitching identities across chunks belongs to `pipeline/steps/speakers.py`,
which already reconciles labels, and would be wrong to fake here.

**Context is lost at the boundary.** The model transcribing chunk 2 has not
heard the sentence that runs into it. That is a real accuracy cost at one point
per half hour, and the reason the split goes in silence, where a sentence is
least likely to be running.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: How far back from the limit to look for a silence before giving up and
#: cutting on the clock. Two minutes: long enough to find a pause in any real
#: interview, short enough that chunks stay near the limit rather than halving.
SEARCH_WINDOW_S = 120.0

#: Silence shorter than this is a breath, not a boundary.
MIN_SILENCE_S = 0.35

#: Below this, silencedetect hears room tone as speech. -35 dB is quiet enough
#: for a treated room and forgiving enough for a noisy location recording.
SILENCE_DB = -35


@dataclass(frozen=True)
class Chunk:
    index: int
    start_s: float
    end_s: float
    path: Path | None = None

    @property
    def offset_ms(self) -> int:
        return int(round(self.start_s * 1000))


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise RuntimeError(
            f"could not read the duration of {path.name}: {proc.stderr.strip()}"
        )


def find_silences(path: Path, *, noise_db: int = SILENCE_DB,
                  min_s: float = MIN_SILENCE_S) -> list[tuple[float, float]]:
    """(start, end) of every silence ffmpeg can hear. Empty on failure.

    Empty is a safe answer: `plan` falls back to cutting on the clock, which is
    worse but not broken. A splitter that raises because the audio had no
    detectable pauses would refuse to transcribe continuous speech.
    """
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(m) for m in re.findall(r"silence_start:\s*(-?[\d.]+)",
                                           proc.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*(-?[\d.]+)",
                                         proc.stderr)]
    return [(s, e) for s, e in zip(starts, ends) if e > s]


def plan(duration_s: float, limit_s: float,
         silences: list[tuple[float, float]],
         *, search_window_s: float = SEARCH_WINDOW_S) -> list[Chunk]:
    """Where to cut. Pure — the reason splitting is testable without ffmpeg."""
    if limit_s <= 0 or duration_s <= limit_s:
        return [Chunk(0, 0.0, duration_s)]

    chunks: list[Chunk] = []
    start = 0.0
    while duration_s - start > limit_s:
        target = start + limit_s
        floor = max(start + limit_s - search_window_s, start + 1.0)
        # The latest silence that ends before the limit, and the middle of it:
        # a cut at the very edge of a pause is a cut next to a word.
        candidates = [(s, e) for s, e in silences if floor <= s and e <= target]
        cut = (candidates[-1][0] + candidates[-1][1]) / 2 if candidates else target
        chunks.append(Chunk(len(chunks), start, cut))
        start = cut
    chunks.append(Chunk(len(chunks), start, duration_s))
    return chunks


def slice_audio(path: Path, chunk: Chunk, out_dir: Path) -> Path:
    """One chunk as its own WAV, at the same 16 kHz mono the stage produced."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{path.stem}.c{chunk.index:02d}.wav"
    if out.exists():
        return out
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{chunk.start_s:.3f}", "-to", f"{chunk.end_s:.3f}",
         "-i", str(path), "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(out)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"could not split {path.name} at {chunk.start_s:.1f}s: "
            f"{proc.stderr.strip()}")
    return out


def split(path: Path, limit_s: float, out_dir: Path) -> list[Chunk]:
    """The whole operation: measure, find pauses, plan, cut."""
    duration = probe_duration(path)
    if duration <= limit_s:
        return [Chunk(0, 0.0, duration, path)]
    planned = plan(duration, limit_s, find_silences(path))
    return [Chunk(c.index, c.start_s, c.end_s, slice_audio(path, c, out_dir))
            for c in planned]


def merge(results: list, chunks: list[Chunk]):
    """Stitch per-chunk results into one, shifting times back onto the source.

    Speaker labels are namespaced rather than merged, for the reason in the
    module docstring. Cost and duration add up; the language is the first
    chunk's, since auto-detection on a later chunk of the same interview
    disagreeing with the first is not a thing to average.
    """
    from .base import ASRResult, Word

    if len(results) == 1:
        # Nothing was split, so nothing needs shifting or namespacing. Returning
        # the result untouched keeps a short file's speaker labels the ones the
        # vendor gave, rather than `c0:spk_1` for a chunk that never happened.
        return results[0]

    words: list[Word] = []
    seconds = 0.0
    cost = 0.0
    latency = 0
    priced = True
    estimated = False
    for result, chunk in zip(results, chunks):
        for w in result.words:
            words.append(Word(
                text=w.text,
                start_ms=w.start_ms + chunk.offset_ms,
                end_ms=w.end_ms + chunk.offset_ms,
                confidence=w.confidence,
                speaker=(f"c{chunk.index}:{w.speaker}" if w.speaker else ""),
            ))
        seconds += result.audio_seconds
        cost += result.cost_usd
        latency += result.latency_ms
        priced = priced and result.priced
        estimated = estimated or result.cost_estimated

    first = results[0]
    return ASRResult(
        words=words, language=first.language, provider=first.provider,
        model=first.model, audio_seconds=seconds, cost_usd=cost,
        priced=priced, cost_estimated=estimated, latency_ms=latency,
        chunks=len(results),
    )
