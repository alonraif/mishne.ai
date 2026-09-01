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
costs, and for why each chunk is banked as it comes in rather than only when
they are all in.

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
from ..logging import get_logger
from .base import ASRError, ASRResult, Word
from .transport import (delete, post_capture_headers, post_json,
                        timeout_for)

BASE_URL = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
API_KEY_ENV = "GEMINI_API_KEY"

log = get_logger(__name__)


def _dump_raw(audio: Path, data: dict) -> None:
    """`MISHNE_ASR_DEBUG_RAW=1` writes the response beside the audio.

    A vendor's actual response shape is a fact, and reading it once beats
    reading the documentation three times — this endpoint's docs do not
    describe its usage fields at all, which is why a real cost may be arriving
    under a name nothing here looks for and quietly reading as an estimate.

    Off by default and deliberately not on in production: a raw response
    contains the transcript, which is customer content, and nothing that holds
    customer content belongs on a worker's disk by default.
    """
    if os.environ.get("MISHNE_ASR_DEBUG_RAW", "") in ("", "0"):
        return
    import json

    out = audio.with_suffix(".gemini-raw.json")
    try:
        out.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    except Exception:  # noqa: BLE001 — a debugging aid never fails a job
        pass


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
        # Banked per chunk, but only when the audio really was split: unsplit
        # audio has `chunk.path` == this stage's input, and the whole-file cache
        # in `pipeline/steps/transcribe` already covers that case.
        results = [self._chunk(c, language=language, diarize=diarize,
                               bank=len(chunks) > 1)
                   for c in chunks]
        return chunking.merge(results, chunks)

    def _chunk(self, chunk: chunking.Chunk, *, language: str | None,
               diarize: bool, bank: bool) -> ASRResult:
        """One chunk, transcribed once however often stage 3 is retried.

        The retry that matters is the runner's, which re-enters this provider
        from scratch. Without the bank, an hour of audio that fails on its
        second half pays for its first half once per attempt — and spends so
        long doing it that the request which is actually failing gets one try
        instead of three.
        """
        if bank:
            banked = chunking.memo_read(chunk)
            if banked is not None:
                log.info("asr.chunk_banked", chunk=chunk.index,
                         words=len(banked.words))
                return banked
        result = self._transcribe_one(chunk.path, language=language,
                                      diarize=diarize)
        if bank:
            chunking.memo_write(chunk, result)
        return result

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
            _dump_raw(audio, data)
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

        usage = _usage(data)
        audio_tokens = (
            _by_modality(usage, "input_tokens_by_modality", "audio")
            or _count(usage, "total_input_tokens", "audioTokenCount",
                      "promptTokenCount", "input_tokens")
        )
        # Reported as zero on a transcription, and reported *explicitly* —
        # alongside tool-use and thought tokens, also zero, with input broken
        # down by modality. This is a complete accounting saying the transcript
        # is not billed as output, not an absent field. It contradicts the
        # pricing page's text-output line, which is why the first invoice is
        # worth checking against `report --baseline`.
        text_tokens = _count(usage, "total_output_tokens", "candidatesTokenCount",
                             "outputTokenCount", "output_tokens")
        # Google caches audio across requests, so re-running the same file is
        # cheaper than the first time. Recorded because a benchmark that runs
        # one sample repeatedly would otherwise report a price no customer ever
        # pays — the same trap `report.transcription_baseline` excludes cached
        # steps for. Priced at full rate: the cache discount is undocumented
        # here, so this figure is an upper bound rather than a guess.
        cached = _by_modality(usage, "cached_tokens_by_modality", "audio")
        seconds = float(usage.get("audioSeconds") or usage.get("audio_seconds")
                        or 0.0) or fallback_seconds
        cost = self.engine.cost_for(seconds, audio_tokens=audio_tokens,
                                    text_tokens=text_tokens)
        if audio_tokens:
            log.info("asr.usage", model=self.model_name,
                     audio_tokens=audio_tokens, text_tokens=text_tokens,
                     cached_audio_tokens=cached,
                     tokens_per_second=round(audio_tokens / max(seconds, 1), 2))

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


#: Where a usage object might be. `usage` is what a real response carries; the
#: camelCase spellings are kept because the endpoint's documentation describes
#: none of this and a model days old may yet change its mind.
_USAGE_KEYS = ("usage", "usageMetadata", "usage_metadata")


def _usage(data: dict) -> dict:
    for key in _USAGE_KEYS:
        found = data.get(key)
        if isinstance(found, dict) and found:
            return found
    # Some shapes hang it off the step rather than the interaction.
    for step in data.get("steps") or []:
        for key in _USAGE_KEYS:
            found = step.get(key)
            if isinstance(found, dict) and found:
                return found
    return {}


def _by_modality(usage: dict, key: str, modality: str) -> int:
    """One modality's tokens out of a `*_by_modality` breakdown.

    A real response reports input as `[{"modality": "audio", "tokens": 5551},
    {"modality": "text", "tokens": 1}]` — the single text token being the
    prompt. Audio is what this is billed on and mixing the two into one number
    would price a modality at the wrong rate, so they are taken apart here.
    """
    for entry in usage.get(key) or []:
        if isinstance(entry, dict) and entry.get("modality") == modality:
            return int(entry.get("tokens") or 0)
    return 0


def _count(usage: dict, *names: str) -> int:
    for name in names:
        value = usage.get(name)
        if isinstance(value, (int, float)) and value:
            return int(value)
    return 0


def _offset_ms(value) -> int:
    """`"1.250s"` — a protobuf Duration as JSON — or a bare number."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(round(float(value) * 1000))
    return int(round(float(str(value).rstrip("s") or 0.0) * 1000))
