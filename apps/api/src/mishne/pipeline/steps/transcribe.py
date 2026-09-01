"""Stage 2 — transcription.

Thin by design: pick a provider, call it, persist the raw result. The
interesting requirements live in `mishne.asr.base` — word-level timestamps,
preserved disfluencies, and boundary accuracy over word error rate.

The default provider is `auto`, which routes by language across the managed
engines in `asr/engines.json` (ADR-0018). Self-hosted Whisper is still here and
still tested — it is what a broadcaster who will not let audio leave the
building runs — but it is roughly a machine hour per source hour, and that is
not a service.

Word timings are put through `asr/timings.py` first. A managed engine
occasionally returns a word whose span is impossible — an end hours into a
46-minute file — and every stage downstream reads a word span as a fact: one bad
`end_ms` became a 23-minute beat and a speaker credited with 49 minutes of
talking on a 46-minute recording. Only the whole sequence can tell, and this is
where the whole sequence is.

A Hebrew transcript is passed through `asr/script.py` next: multilingual
models slip between Hebrew and Arabic on letters the two scripts share, and the
repair belongs here — before the cache — so the stored transcript and every
replay of it agree with the run that produced them.

**Always persist the raw response.** Reprocessing a job must never mean paying
for transcription twice, and every downstream stage is deterministic given a
fixed transcript, so a stored one makes reruns free and reproducible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...asr import ASRResult, get_provider
from ...asr.script import normalise_hebrew
from ...asr.timings import sanitise as sanitise_timings
from ...logging import get_logger

log = get_logger(__name__)


def run(audio: Path, out_dir: Path, provider: str = "auto",
        language: str | None = None, **provider_kwargs) -> ASRResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / f"{audio.stem}.asr.json"

    if cache.exists():
        # Sanitised on the way out as well as the way in: the invariant has to
        # hold for transcripts cached before this code existed, and re-paying
        # for transcription to get it would be the wrong trade by orders of
        # magnitude. `sanitise` is idempotent, so this is free on a cache
        # written by the branch below.
        cached = ASRResult.from_dict(json.loads(cache.read_text()))
        sanitise_timings(cached)
        return cached

    result = get_provider(provider, **provider_kwargs).transcribe(
        audio, language=language
    )
    # Both repairs happen before the cache, so the stored transcript is the
    # repaired one and every replay of it agrees with the run that produced it.
    sanitise_timings(result)
    if os.environ.get("MISHNE_ASR_TRANSLITERATE", "1") not in ("0", "false"):
        repaired = normalise_hebrew(result)
        if repaired:
            log.info("asr.script_normalised", words=repaired,
                     total=len(result.words), language=result.language)
    cache.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1))
    return result
