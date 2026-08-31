"""Gemini 3.5 Transcribe — the engine that speaks Hebrew.

Hebrew is a first-class target for this product (see `language.py`) and it is
absent from xAI's language list. Of the managed engines with word-level
timestamps, this is the one that publishes `he-IL` — and at ~$0.30 per source
hour it is also cheaper than the OpenAI model that meets the timestamp
requirement. So Hebrew routes here, and so does anything else xAI does not
claim, on the strength of 85+ languages.

## The request carries three things the pipeline cannot work without

    mode.type = "verbatim"                  disfluencies kept, no clean-up
    timestamp_granularities = ["word"]      cuts land between words
    diarization_mode = "speaker"            who is talking, up to 8 people

Google's own documentation warns that turning timestamps on costs some
transcription accuracy. That trade is not optional here: segment-level output is
unusable for cutting (asr/base.py), so the accuracy cost is the price of the
product working at all — and it is a thing to measure against Whisper on the
same material, not to assume away.

## Two operational facts

**Both of those flags cap a request at 30 minutes**, against an hour without
them. Longer material is split by `asr/chunking.py`; see there for what a seam
costs.

**Audio goes through the Files API**, because inline bytes are for clips of a
few seconds. That puts customer media on Google's servers, where it would
otherwise sit for 48 hours, so the uploaded file is deleted as soon as the
transcript is in hand — media retention is a promise this product makes
(docs/architecture/04-security.md), and "the vendor expires it eventually" is
not the same promise.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import catalog, chunking
from .base import ASRError, ASRResult, Word
from .transport import (delete, post_capture_headers, post_json,
                        timeout_for)

BASE_URL = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
API_KEY_ENV = "GEMINI_API_KEY"


class GeminiProvider:
    name = "google"

    def __init__(self, model: str = "gemini-3.5-transcribe",
                 api_key: str | None = None, work_dir: Path | None = None,
                 keyterms: str = "", **_ignored):
        self.model_name = model
        self.engine = catalog.find(model, "google")
        self.api_key = api_key or os.environ.get(API_KEY_ENV, "")
        #: Where split chunks are written. Falls back to beside the audio,
        #: which for a worker is the asset directory it already owns.
        self.work_dir = Path(work_dir) if work_dir else None
        self.keyterms = keyterms

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def _headers(self) -> dict:
        return {"x-goog-api-key": self.api_key}

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        if not self.available:
            raise ASRError(f"{API_KEY_ENV} is not set", retryable=False)

        limit = float(self.engine.max_seconds or 0)
        chunks = (chunking.split(audio, limit,
                                 self.work_dir or audio.parent / "chunks")
                  if limit else [chunking.Chunk(0, 0.0, 0.0, audio)])
        results = [self._transcribe_one(c.path, language=language,
                                        diarize=diarize)
                   for c in chunks]
        return chunking.merge(results, chunks)

    # ── one request ────────────────────────────────────────────────────────

    def _transcribe_one(self, audio: Path, *, language: str | None,
                        diarize: bool) -> ASRResult:
        uri, file_name = self._upload(audio)
        try:
            mode = {
                "type": "verbatim",
                "timestamp_granularities": ["word"],
            }
            if diarize:
                mode["diarization_mode"] = "speaker"
            body = {
                "model": self.model_name,
                "input": [{"type": "audio", "uri": uri,
                           "mime_type": "audio/wav"}],
                "generation_config": {"transcription_config": {"mode": mode}},
            }
            if language:
                # A named language stops auto-detection choosing Arabic for
                # Hebrew, which is a plausible mistake between two RTL Semitic
                # scripts and an expensive one to notice late.
                body["generation_config"]["transcription_config"]["language_codes"] = [
                    language if "-" in language else language.lower()
                ]
            if self.keyterms:
                body["generation_config"]["transcription_config"]["vocabulary"] = [
                    t.strip() for t in self.keyterms.split(",") if t.strip()
                ]

            seconds = audio.stat().st_size / 32_000
            data, ms = post_json(f"{BASE_URL}/v1beta/interactions",
                                 self._headers, body,
                                 timeout=timeout_for(seconds))
        finally:
            if file_name:
                delete(f"{BASE_URL}/v1beta/{file_name}", self._headers)
        return self._parse(data, ms, language, fallback_seconds=seconds)

    def _upload(self, audio: Path) -> tuple[str, str]:
        """Files API, resumable protocol: start, then upload-and-finalise."""
        size = audio.stat().st_size
        _, headers, _ = post_capture_headers(
            f"{BASE_URL}/upload/v1beta/files",
            {**self._headers,
             "x-goog-upload-protocol": "resumable",
             "x-goog-upload-command": "start",
             "x-goog-upload-header-content-length": str(size),
             "x-goog-upload-header-content-type": "audio/wav",
             "content-type": "application/json"},
            {"file": {"display_name": audio.name}},
        )
        upload_url = headers.get("x-goog-upload-url")
        if not upload_url:
            raise ASRError(
                "the Files API did not return an upload URL", retryable=True)

        payload, _, _ = post_capture_headers(
            upload_url,
            {**self._headers,
             "content-length": str(size),
             "x-goog-upload-offset": "0",
             "x-goog-upload-command": "upload, finalize"},
            audio.read_bytes(),
            timeout=timeout_for(size / 32_000),
        )
        info = payload.get("file") or {}
        uri = info.get("uri")
        if not uri:
            raise ASRError(f"upload returned no file uri: {payload}",
                           retryable=True)
        return uri, info.get("name", "")

    # ── response ───────────────────────────────────────────────────────────

    def _parse(self, data: dict, latency_ms: int, language: str | None,
               *, fallback_seconds: float) -> ASRResult:
        words: list[Word] = []
        detected = ""
        for step in data.get("steps") or []:
            for block in step.get("content") or []:
                if block.get("type") != "text":
                    continue
                detected = detected or block.get("language", "")
                for note in block.get("annotations") or []:
                    if note.get("type") != "word_info":
                        continue
                    text = (note.get("text") or "").strip()
                    if not text:
                        continue
                    words.append(Word(
                        text=text,
                        start_ms=_offset_ms(note.get("start_offset")),
                        end_ms=_offset_ms(note.get("end_offset")),
                        confidence=float(note.get("confidence", 1.0)),
                        speaker=note.get("speaker") or "",
                    ))

        if not words:
            # Same rule as the xAI provider: a transcript with no word
            # timestamps is not a cheaper answer, it is no answer. Returning it
            # empty would read downstream as silent audio.
            raise ASRError(
                "no word_info annotations in the response; word timestamps "
                "were requested and are required", retryable=False)

        usage = data.get("usage") or data.get("usage_metadata") or {}
        audio_tokens = int(usage.get("audio_input_tokens")
                           or usage.get("input_tokens") or 0)
        text_tokens = int(usage.get("output_tokens")
                          or usage.get("text_output_tokens") or 0)
        seconds = float(usage.get("audio_seconds") or 0.0) or fallback_seconds
        cost = self.engine.cost_for(seconds, audio_tokens=audio_tokens,
                                    text_tokens=text_tokens)

        return ASRResult(
            words=words,
            language=detected or language or "",
            provider=self.name,
            model=self.model_name,
            audio_seconds=seconds,
            cost_usd=cost.value,
            priced=cost.priced,
            cost_estimated=cost.estimated,
            latency_ms=latency_ms,
        )


def _offset_ms(value) -> int:
    """`"1.250s"` — a protobuf Duration as JSON — or a bare number."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 1000))
    return int(round(float(str(value).rstrip("s") or 0.0) * 1000))
