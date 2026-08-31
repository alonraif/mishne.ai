"""xAI Grok speech-to-text — the cheap half of the routing decision.

$0.10 per source hour batch, against roughly one machine-hour per source hour of
CPU Whisper. That ratio is the whole argument: transcription stopped being a
capacity problem and became a line item.

## Three request fields carry the pipeline's actual requirements

    filler_words=true    every "um" kept. Removing filler is this system's
                         job (asr/base.py) and it cannot do it if the ASR
                         already did. This is the field the whole product
                         depends on and it defaults to off.
    format=false         no inverted text normalisation — no "twenty twenty
                         six" becoming "2026". A rewritten word has timestamps
                         that no longer describe the audio under it, and every
                         cut lands between words.
    diarize=true         speaker labels, which stage 4 needs to see a speaker
                         change and stage 7 needs for speaker_priority.

## What the `language` field does, and what it does not

xAI's documentation says the language code drives text formatting rather than
transcription, and that the model handles audio in any language. If true,
Hebrew would run here at a third of Gemini's price. It is untested, so
`asr/routing.py` does not act on it — see the note in `engines.json`.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import catalog
from .base import ASRError, ASRResult, Word
from .transport import post_multipart, timeout_for

BASE_URL = os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1")
API_KEY_ENV = "XAI_API_KEY"


class XAIProvider:
    name = "xai"

    def __init__(self, model: str = "grok-stt", api_key: str | None = None,
                 keyterms: str = "", **_ignored):
        self.model_name = model
        self.engine = catalog.find(model, "xai")
        self.api_key = api_key or os.environ.get(API_KEY_ENV, "")
        #: Names, jargon and product terms the material is full of. Free
        #: accuracy on exactly the words a transcript gets wrong and an editor
        #: notices — and the only vendor knob here that touches quality.
        self.keyterms = keyterms

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        if not self.available:
            raise ASRError(f"{API_KEY_ENV} is not set", retryable=False)

        size = audio.stat().st_size
        if self.engine.max_bytes and size > self.engine.max_bytes:
            raise ASRError(
                f"{audio.name} is {size / 1e6:.0f} MB; the endpoint takes "
                f"{self.engine.max_bytes / 1e6:.0f} MB", retryable=False)

        fields = {
            "format": False,        # see the module docstring — both of these
            "filler_words": True,   # are requirements, not preferences
            "diarize": diarize,
        }
        if language:
            fields["language"] = language.split("-")[0].lower()
        if self.keyterms:
            fields["keyterm"] = self.keyterms

        # The file is on local disk and its duration is not known here, so the
        # timeout is sized from the file: 16 kHz mono PCM is 32 kB/s, which is
        # what `pipeline/steps/audio.py` writes.
        data, ms = post_multipart(
            f"{BASE_URL}/stt",
            {"authorization": f"Bearer {self.api_key}"},
            fields, "file", audio,
            timeout=timeout_for(size / 32_000),
        )
        return self._parse(data, ms, language)

    def _parse(self, data: dict, latency_ms: int,
               language: str | None) -> ASRResult:
        raw_words = data.get("words")
        if raw_words is None:
            # A transcript with no word array is not a usable answer: stage 4
            # cuts between words and has nothing to cut on. Fail loudly rather
            # than returning an empty transcript, which reads downstream as
            # "the audio was silent".
            raise ASRError(
                "response carried no word timestamps; the pipeline cannot cut "
                "on segment-level output", retryable=False)

        words: list[Word] = []
        for w in raw_words:
            text = (w.get("text") or w.get("word") or "").strip()
            if not text:
                continue
            words.append(Word(
                text=text,
                start_ms=int(round(float(w.get("start", 0.0)) * 1000)),
                end_ms=int(round(float(w.get("end", 0.0)) * 1000)),
                confidence=float(w.get("confidence", 1.0)),
                # Speaker ids come back as integers; every downstream stage
                # keys on a string, and `""` means "not separated".
                speaker=("" if w.get("speaker") is None
                         else f"spk_{w['speaker']}"),
            ))

        seconds = float(data.get("duration") or 0.0)
        if not seconds and words:
            # The vendor is the source of truth for what it will bill; the last
            # word's end is a floor for it, and a floor is better than zero,
            # which would make the job read as free.
            seconds = words[-1].end_ms / 1000.0
        cost = self.engine.cost_for(seconds)

        return ASRResult(
            words=words,
            language=data.get("language") or language or "",
            provider=self.name,
            model=self.model_name,
            audio_seconds=seconds,
            cost_usd=cost.value,
            priced=cost.priced,
            cost_estimated=cost.estimated,
            latency_ms=latency_ms,
        )
