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


class ASRError(RuntimeError):
    """A transcription call failed.

    Carries `retryable` for the same reason `LLMError` does: the router has to
    know whether trying the other vendor could possibly help. A 400 because the
    audio is malformed fails identically everywhere, and walking two keys to
    discover that wastes the operator's money and buries the real error.
    """

    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.retryable = retryable


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
    #: Audio duration as the provider measured it, in seconds. Managed engines
    #: bill by this, so it is the quantity an invoice is checked against — and
    #: `duration_ms` below is NOT it: the last word ends before the audio does.
    audio_seconds: float = 0.0
    #: What this transcription cost, in USD. 0.0 with `priced=False` means
    #: unknown, never free — the distinction `llm/catalog.py` exists to keep.
    cost_usd: float = 0.0
    priced: bool = True
    #: True when `cost_usd` came from published rates applied to ASSUMED token
    #: counts because the vendor reported none. An estimate that is allowed to
    #: read as a measurement is how an estimator stays "calibrated" while every
    #: line under it is wrong — see docs/notes/c1-first-cost-numbers.
    cost_estimated: bool = False
    latency_ms: int = 0
    #: How many requests this transcript took. >1 means the audio was longer
    #: than the engine's limit and was split (asr/chunking.py), which is worth
    #: knowing when a word boundary looks wrong near a chunk seam.
    chunks: int = 1

    @property
    def duration_ms(self) -> int:
        return self.words[-1].end_ms if self.words else 0

    @property
    def engine(self) -> str:
        return f"{self.provider}/{self.model}"

    @property
    def usd_per_source_hour(self) -> float | None:
        """The number the GPU-or-CPU decision was blocked on, per transcript."""
        if not self.priced or not self.audio_seconds:
            return None
        return self.cost_usd / (self.audio_seconds / 3600.0)

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "language": self.language,
            "audioSeconds": round(self.audio_seconds, 3),
            "costUsd": round(self.cost_usd, 6),
            "priced": self.priced,
            "costEstimated": self.cost_estimated,
            "latencyMs": self.latency_ms,
            "chunks": self.chunks,
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
            audio_seconds=d.get("audioSeconds", 0.0),
            # A cached transcript costs nothing to serve, and the cost of the
            # run that produced it belongs to that run. Reading it back as a
            # fresh charge would bill a re-run for work ADR-0008 exists to
            # avoid, so the money does not survive the round trip: only the
            # duration does, which is what a per-source-hour figure needs.
            cost_usd=0.0,
            priced=True,
            latency_ms=0,
            chunks=d.get("chunks", 1),
        )


class ASRProvider(Protocol):
    name: str

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        ...


#: The default. Routes by language across the managed engines in
#: `engines.json` — see `asr/routing.py` for why this replaced self-hosted
#: Whisper as the default, and ADR-0018.
DEFAULT_PROVIDER = "auto"


def get_provider(name: str, **kwargs) -> ASRProvider:
    if name in ("auto", "managed"):
        from .routing import RoutedASR
        return RoutedASR(**kwargs)
    if name == "xai":
        from .xai_provider import XAIProvider
        return XAIProvider(**kwargs)
    if name in ("google", "gemini"):
        from .gemini_provider import GeminiProvider
        return GeminiProvider(**kwargs)
    if name == "faster-whisper":
        from .faster_whisper_provider import FasterWhisperProvider
        return FasterWhisperProvider(**kwargs)
    if name == "replay":
        from .replay import ReplayProvider
        return ReplayProvider(**kwargs)
    raise ValueError(f"unknown ASR provider: {name}")
