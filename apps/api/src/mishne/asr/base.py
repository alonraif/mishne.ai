"""ASR behind one interface.

The provider is a configuration choice, not an architectural one — see
[ADR-0003](../../../../docs/adr/0003-managed-asr-behind-an-interface.md). Some
broadcasters will refuse third-party transcription outright, and that scenario
is exactly why this boundary exists: swapping to a self-hosted model is a config
change, not a rewrite.

Non-obvious requirements every provider must honour:

- **Word-level timestamps.** Segment-level is useless; cuts land between words.
- **Disfluencies preserved.** Most managed APIs default to "smart formatting"
  that quietly deletes filler and repairs false starts. Turn it off. Removing
  "um" is mishne.ai's job and it cannot do it without knowing where they are.
- **Timestamp boundary accuracy matters more than word error rate.** A provider
  at 3% WER with sloppy word boundaries produces worse cuts than one at 5% with
  tight ones. Nobody publishes this; measure it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Word:
    text: str
    start_ms: int
    end_ms: int
    confidence: float = 1.0
    speaker: str = ""

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class ASRResult:
    words: list[Word]
    language: str
    provider: str
    model: str
    raw: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return self.words[-1].end_ms if self.words else 0

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "words": [
                {"t": w.text, "s": w.start_ms, "e": w.end_ms,
                 "c": round(w.confidence, 3), "spk": w.speaker}
                for w in self.words
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ASRResult":
        return cls(
            words=[Word(w["t"], w["s"], w["e"], w.get("c", 1.0), w.get("spk", ""))
                   for w in d["words"]],
            language=d.get("language", "en"),
            provider=d.get("provider", "replay"),
            model=d.get("model", ""),
        )


class ASRProvider(Protocol):
    name: str

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        ...


def get_provider(name: str, **kwargs) -> ASRProvider:
    if name == "faster-whisper":
        from .faster_whisper_provider import FasterWhisperProvider
        return FasterWhisperProvider(**kwargs)
    if name == "replay":
        from .replay import ReplayProvider
        return ReplayProvider(**kwargs)
    raise ValueError(f"unknown ASR provider: {name}")
