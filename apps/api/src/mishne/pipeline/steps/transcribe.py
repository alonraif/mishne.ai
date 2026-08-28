"""Stage 2 — transcription.

Thin by design: pick a provider, call it, persist the raw result. The
interesting requirements live in `mishne.asr.base` — word-level timestamps,
preserved disfluencies, and boundary accuracy over word error rate.

**Always persist the raw response.** Reprocessing a job must never mean paying
for transcription twice, and every downstream stage is deterministic given a
fixed transcript, so a stored one makes reruns free and reproducible.
"""

from __future__ import annotations

import json
from pathlib import Path

from ...asr import ASRResult, get_provider


def run(audio: Path, out_dir: Path, provider: str = "faster-whisper",
        language: str | None = None, **provider_kwargs) -> ASRResult:
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = out_dir / f"{audio.stem}.asr.json"

    if cache.exists():
        return ASRResult.from_dict(json.loads(cache.read_text()))

    result = get_provider(provider, **provider_kwargs).transcribe(
        audio, language=language
    )
    cache.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1))
    return result
