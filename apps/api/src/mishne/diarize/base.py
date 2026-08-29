"""Speaker diarization — who spoke when, on material with only one track.

## Why this is a separate thing from attribution

Multi-track material needs no model at all. Each person has a microphone, the
loudest track at any moment is the person speaking, and attribution is
arithmetic — deterministic, explainable, and right. That path lives in
`pipeline/steps/speakers.py` and stays there.

Single-track material has none of that, and the pipeline used to answer it by
reporting one speaker and marking the answer reliable. On real footage — a
presenter interviewing two designers — that put every line of a three-way
conversation in one mouth and said it was trustworthy. Silence would have been
better; this module is the honest answer.

## What it can and cannot tell you

Diarization separates voices. It does **not** identify people: the output is
"three distinct speakers", never their names, and naming stays a thing a person
does in the UI.

It is also not free of assumptions about the recording. Speaker embeddings
encode the microphone and the room along with the voice, so on material
assembled from several sources the clusters follow the *sources* rather than the
speakers. That is not a hypothetical:

    the reference Hebrew segment, 22 clips from two cameras, diarized whole
      -> 5 speakers, switching almost exactly on the clip boundaries
    the same audio, one clip in isolation
      -> 3 speakers, matching the conversation by ear

So a sequence is diarized **per source region**, and the per-region clusters are
matched afterwards on channel-compensated embeddings. See `sherpa_provider`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass
class Turn:
    """One stretch of one voice."""

    start_ms: int
    end_ms: int
    speaker: str

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class DiarizationResult:
    turns: list[Turn]
    provider: str = ""
    model: str = ""
    reliable: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def speaker_count(self) -> int:
        return len({t.speaker for t in self.turns})

    def speaker_at(self, start_ms: int, end_ms: int) -> str:
        """Whose word this is — the voice covering most of its span.

        Overlap rather than midpoint: a word straddling a turn boundary belongs
        to whoever says most of it, and a midpoint test flips on a millisecond.
        """
        best, best_overlap = "", 0
        for t in self.turns:
            overlap = min(end_ms, t.end_ms) - max(start_ms, t.start_ms)
            if overlap > best_overlap:
                best, best_overlap = t.speaker, overlap
        return best

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "reliable": self.reliable,
            "notes": self.notes,
            "speakerCount": self.speaker_count,
            "turns": [{"s": t.start_ms, "e": t.end_ms, "spk": t.speaker}
                      for t in self.turns],
        }


@runtime_checkable
class DiarizationProvider(Protocol):
    """The seam a managed service would slot into. See ADR-0003."""

    name: str

    def diarize(self, audio_path: Path,
                regions: list[tuple[int, int]] | None = None
                ) -> DiarizationResult:
        """Separate voices in `audio_path`.

        `regions` are the material's own source boundaries in milliseconds —
        the clips of a sequence. Supplying them is what keeps the clustering
        about people rather than microphones. Omit for a single continuous
        recording.
        """
        ...
