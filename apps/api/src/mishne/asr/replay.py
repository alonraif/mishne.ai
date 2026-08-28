"""Replay a stored ASR result instead of calling a provider.

Two reasons this is not just a test double.

**Cost.** Reprocessing a job must never mean paying for transcription twice.
Every real run persists its raw provider response, and replay is how that gets
used — reruns, prompt changes, solver changes all reuse the same transcript.

**Testability.** The pipeline downstream of stage 2 is deterministic. Being able
to pin the transcript makes every test of stages 3-12 reproducible and offline.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import ASRResult


class ReplayProvider:
    name = "replay"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        return ASRResult.from_dict(json.loads(self.path.read_text()))
