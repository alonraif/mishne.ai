"""Which engine transcribes which language, and what it cost.

The sibling of `llm/router.py`, and the same division of labour: the provider
makes one call, the router decides who gets it, fails over, and records what
happened.

## The decision

    language xAI publishes        →  xai/grok-stt        $0.10 / source hour
    Hebrew, or anything else      →  gemini-3.5-transcribe  ~$0.30 / source hour
    unspecified language          →  gemini-3.5-transcribe

Cost is the tiebreak, not the rule. Among the engines that *can* transcribe the
material, the cheapest wins; an engine that does not publish the language is
never cheaper, because a fluent transcript of the wrong words costs a re-run and
an editor's afternoon. Unspecified is treated as unidentified rather than as
English for the same reason — see `Engine.speaks`.

## Failover is not free here, and is bounded on purpose

The LLM router walks up to three models because a failed prompt costs a prompt.
A failed transcription costs an hour of audio at both vendors, so failover here
happens only when the first engine could not have produced an answer — a key
that is not set, a 5xx, a rate limit, a timeout. A 400 is our request being
wrong and fails the same way everywhere.

## Why this is the default and Whisper is not

ADR-0003 put ASR behind an interface so the provider is a configuration choice.
This is that choice being made differently: CPU Whisper is roughly a machine
hour per source hour, which is fine for a proof of concept and is not a service.
Self-hosting stays supported and stays the answer for a broadcaster who will not
let audio leave the building — it is `--asr faster-whisper`, one flag, still
tested.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import catalog
from .base import ASRError, ASRResult

#: Vendor -> the environment variable holding its key. The same names the LLM
#: providers use, deliberately: one key per vendor, not one per subsystem.
API_KEYS = {"xai": "XAI_API_KEY", "google": "GEMINI_API_KEY"}


def available_providers() -> list[str]:
    return [name for name, env in API_KEYS.items() if os.environ.get(env)]


def _pinned() -> str:
    """`MISHNE_ASR_ENGINE=google/gemini-3.5-transcribe` skips routing."""
    return os.environ.get("MISHNE_ASR_ENGINE", "")


def _xai_takes_any_language() -> bool:
    """Opt in to xAI's claim that it transcribes languages it does not list.

    Off by default. If it holds, Hebrew gets three times cheaper — which makes
    it worth measuring against Gemini on the same material and not worth
    assuming from a documentation line. See engines.json.
    """
    return os.environ.get("MISHNE_ASR_XAI_ANY_LANGUAGE", "") not in ("", "0")


def plan(language: str | None, *, engines: list[catalog.Engine] | None = None,
         have: list[str] | None = None) -> list[catalog.Engine]:
    """Engines that can transcribe `language`, cheapest first."""
    pinned = _pinned()
    if pinned:
        provider, _, engine_id = pinned.rpartition("/")
        return [catalog.find(engine_id, provider)]

    pool = engines if engines is not None else catalog.load()
    keys = set(have if have is not None else available_providers())
    usable = [
        e for e in pool
        if e.provider in keys
        and e.word_timestamps                      # non-negotiable, asr/base.py
        and (e.speaks(language)
             or (e.provider == "xai" and _xai_takes_any_language()))
    ]
    usable.sort(key=lambda e: (e.rank_cost(), e.key))
    return usable


class RoutedASR:
    """An `ASRProvider` that picks the engine per language and records cost."""

    name = "auto"

    def __init__(self, work_dir: Path | None = None, keyterms: str = "",
                 ledger: object = None, **_ignored):
        self.work_dir = work_dir
        self.keyterms = keyterms
        #: The router's ledger, when a job has one. Transcription is the largest
        #: single cost in a job and until this it was the one call nobody
        #: recorded — so a job's spend was its model calls and a blank where the
        #: expensive half went (C3, "transcription cost baseline per source
        #: hour"). One `CallRecord` per engine call makes it a row in
        #: `job_llm_calls` like everything else, per step, per asset.
        self.ledger = ledger
        self.engine_used: catalog.Engine | None = None

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        from .base import get_provider

        candidates = plan(language)
        if not candidates:
            raise ASRError(_nothing_available(language), retryable=False)

        first, last_error = candidates[0], None
        for engine in candidates[:2]:
            provider = get_provider(
                engine.provider, model=engine.id,
                work_dir=self.work_dir, keyterms=self.keyterms)
            try:
                result = provider.transcribe(audio, language=language,
                                             diarize=diarize)
            except ASRError as exc:
                self._record_failure(engine, exc)
                last_error = exc
                if not exc.retryable:
                    raise
                continue
            self.engine_used = engine
            self._record(result, fell_back_from=(
                "" if engine is first else first.key))
            return result

        raise ASRError(f"every engine failed for {audio.name}; "
                       f"last: {last_error}", retryable=False)

    # ── the ledger ─────────────────────────────────────────────────────────

    def _record(self, result: ASRResult, *, fell_back_from: str = "") -> None:
        if self.ledger is None:
            return
        from ..llm.base import CallRecord

        self.ledger.add(CallRecord(
            task="transcribe", provider=result.provider, model=result.model,
            ok=True, latency_ms=result.latency_ms, cost_usd=result.cost_usd,
            priced=result.priced,
            # Recorded as its own fact rather than folded into `priced`: an
            # estimate is not a missing price, and a billing path that cannot
            # tell them apart reconciles a guess against an invoice.
            cost_estimated=result.cost_estimated,
            audio_seconds=result.audio_seconds,
            fell_back_from=fell_back_from,
        ))

    def _record_failure(self, engine: catalog.Engine, exc: ASRError) -> None:
        if self.ledger is None:
            return
        from ..llm.base import CallRecord

        self.ledger.add(CallRecord(
            task="transcribe", provider=engine.provider, model=engine.id,
            ok=False, error=type(exc).__name__))


def _nothing_available(language: str | None) -> str:
    have = available_providers()
    if not have:
        return (
            "no transcription engine is configured. Set "
            + " or ".join(sorted(set(API_KEYS.values())))
            + ", or run self-hosted with --asr faster-whisper "
              "(--model-path for an offline model), or --replay a stored "
              "transcript."
        )
    named = language or "unidentified audio"
    return (
        f"none of the configured engines ({', '.join(have)}) publishes support "
        f"for {named}. Set {API_KEYS['google']} — Gemini's list is the wide "
        f"one — or name the language if it is one xAI covers."
    )
