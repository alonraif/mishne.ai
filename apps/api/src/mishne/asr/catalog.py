"""What transcription engines exist, what they cost, and what they speak.

The sibling of `llm/catalog.py` and deliberately the same shape: a data file
(`engines.json`, or the path in `MISHNE_ASR_CATALOG`), an unknown engine is
usable-but-unpriced, and a missing price is never zero.

One thing differs, and it is the reason this file exists rather than a couple of
constants: **transcription is billed by duration, not by tokens.** A per-hour
rate applied to a measured duration is a measurement, not an estimate — the
vendor bills exactly that arithmetic. A per-token rate applied to a duration
*guess* is an estimate, and the two must not arrive at the same place looking
alike. `Cost.estimated` is that line.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

SECONDS_PER_HOUR = 3600.0


@dataclass(frozen=True)
class Cost:
    """What a transcription cost, and how much that number is worth.

    `estimated` means the figure came from published rates applied to assumed
    token counts because the vendor reported none. It is recorded, it is
    reported, and it is never allowed to be mistaken for what the invoice will
    say.
    """

    usd: float | None
    estimated: bool = False

    @property
    def priced(self) -> bool:
        return self.usd is not None

    @property
    def value(self) -> float:
        return self.usd or 0.0


@dataclass(frozen=True)
class Engine:
    id: str
    provider: str
    languages: tuple[str, ...] = ()
    usd_per_hour: float | None = None
    audio_in: float | None = None
    text_out: float | None = None
    usd_per_hour_est: float | None = None
    audio_tokens_per_second: float = 25.0
    text_tokens_per_minute: float = 175.0
    max_seconds: int = 0          # 0 = no published limit
    max_bytes: int = 0
    word_timestamps: bool = True
    diarization: bool = True
    verbatim: bool = True

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.id}"

    def speaks(self, language: str | None) -> bool:
        """Whether this engine is a legal choice for `language`.

        An unspecified language is NOT "any language". It means the material
        has not been identified, and it could be Hebrew — so only an engine
        claiming general coverage may take it. Sending unidentified audio to a
        25-language engine is how Hebrew gets silently transcribed by something
        that does not speak it, and the failure mode is not an error, it is a
        plausible-looking transcript of the wrong words.
        """
        if "*" in self.languages:
            return True
        if not language:
            return False
        return language.split("-")[0].lower() in {
            code.split("-")[0].lower() for code in self.languages
        }

    def cost_for(self, seconds: float, *, audio_tokens: int = 0,
                 text_tokens: int = 0) -> Cost:
        """What `seconds` of audio cost on this engine.

        Three cases, in descending order of how much the number is worth:
        a flat hourly rate on a measured duration (exact); vendor-reported
        token counts at published per-token rates (exact); the vendor's own
        blended per-hour estimate (estimated). No price at all returns
        unpriced, which reads as unknown rather than free.
        """
        if self.usd_per_hour is not None:
            return Cost(seconds / SECONDS_PER_HOUR * self.usd_per_hour)
        if self.audio_in is not None and self.text_out is not None:
            if audio_tokens or text_tokens:
                return Cost((audio_tokens * self.audio_in
                             + text_tokens * self.text_out) / 1_000_000)
            # No usage reported. Reconstruct the vendor's own estimate from the
            # token rates it publishes, rather than from the blended per-hour
            # figure, so a price change to either half is picked up here.
            audio = seconds * self.audio_tokens_per_second
            text = seconds / 60.0 * self.text_tokens_per_minute
            return Cost((audio * self.audio_in + text * self.text_out)
                        / 1_000_000, estimated=True)
        if self.usd_per_hour_est is not None:
            return Cost(seconds / SECONDS_PER_HOUR * self.usd_per_hour_est,
                        estimated=True)
        return Cost(None)

    def rank_cost(self, seconds: float = SECONDS_PER_HOUR) -> float:
        """Cost for ordering. An unpriced engine sorts last, never first."""
        cost = self.cost_for(seconds)
        return float("inf") if cost.usd is None else cost.usd


def _path() -> Path:
    override = os.environ.get("MISHNE_ASR_CATALOG")
    return Path(override) if override else Path(__file__).parent / "engines.json"


def load() -> list[Engine]:
    path = _path()
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [
        Engine(
            id=e["id"], provider=e["provider"],
            languages=tuple(e.get("languages", ())),
            usd_per_hour=e.get("usd_per_hour"),
            audio_in=e.get("audio_in"), text_out=e.get("text_out"),
            usd_per_hour_est=e.get("usd_per_hour_est"),
            audio_tokens_per_second=e.get("audio_tokens_per_second", 25.0),
            text_tokens_per_minute=e.get("text_tokens_per_minute", 175.0),
            max_seconds=e.get("max_seconds", 0),
            max_bytes=e.get("max_bytes", 0),
            word_timestamps=e.get("word_timestamps", True),
            diarization=e.get("diarization", True),
            verbatim=e.get("verbatim", True),
        )
        for e in raw.get("engines", [])
    ]


def verified_on() -> str:
    path = _path()
    if not path.exists():
        return ""
    return json.loads(path.read_text(encoding="utf-8")).get("_verified", "")


def find(engine_id: str, provider: str = "") -> Engine:
    """An engine by id, inventing an unpriced entry when it is uncatalogued."""
    for e in load():
        if e.id == engine_id and (not provider or e.provider == provider):
            return e
    return Engine(id=engine_id, provider=provider or "unknown", languages=("*",))
