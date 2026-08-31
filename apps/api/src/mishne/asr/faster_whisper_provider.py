"""Self-hosted Whisper via faster-whisper (CTranslate2, CPU-capable).

This is the provider that answers "what if a broadcaster will not let their
audio leave the building". It is also the honest baseline to measure managed
providers against.

## Two operational notes that will bite

**The model downloads from HuggingFace on first use.** In a network-restricted
environment that fails with a proxy 403 and no useful message. Either allowlist
`huggingface.co` and `cdn-lfs.huggingface.co`, or fetch the model once and pass
`model_path` to load it from disk. In production the model belongs baked into
the worker image, not downloaded at runtime — a cold start that pulls 1.5 GB is
not a cold start you want on a job SLA.

**CPU transcription is roughly real time or worse.** Measured on 31 Aug 2026:
25.7 minutes of audio took 61.7 minutes on an Apple Silicon laptop at `int8` —
**2.4x real time**, not the 1x this note used to assume. Three hours of rushes
is seven hours of compute. `int8` and a larger `cpu_threads` help; beyond that
this is the argument for either GPU or a managed provider, and ADR-0018 took the
second.

## Disfluencies

`condition_on_previous_text=False` and no VAD filtering on the transcribe call.
Whisper will still tidy some speech — it is trained on written-style targets —
which is a real limitation to measure rather than assume away.

**Measured, and it is worse than the managed engines.** On the same 25.7 minutes
of rushes: 4,542 words against 4,743 and 4,737, and 3.2% filler against 4.3% and
4.2%. It returns 4% less text and proportionally less of the filler inside it.
Removing filler is this system's job and it cannot do it on words that never
arrived.

Its word boundaries are also loose: 170-180 ms from where two independent
managed engines agree the same word is, against 30 ms between those two. That is
four to five frames at 25 fps, and ADR-0003 ranks boundary precision above word
error rate for exactly this reason. See ADR-0018 for the table.
"""

from __future__ import annotations

from pathlib import Path

from .base import ASRResult, Word


class FasterWhisperProvider:
    name = "faster-whisper"

    def __init__(self, model: str = "base", device: str = "cpu",
                 compute_type: str = "int8", model_path: str | None = None,
                 cpu_threads: int = 0):
        from faster_whisper import WhisperModel

        self.model_name = model_path or model
        self.model = WhisperModel(
            self.model_name, device=device, compute_type=compute_type,
            cpu_threads=cpu_threads,
        )

    def transcribe(self, audio: Path, *, language: str | None = None,
                   diarize: bool = True) -> ASRResult:
        segments, info = self.model.transcribe(
            str(audio),
            language=language,
            word_timestamps=True,      # non-negotiable — see asr/base.py
            vad_filter=False,          # stage 3 owns VAD; do not silently trim
            condition_on_previous_text=False,
        )

        words: list[Word] = []
        for seg in segments:
            for w in (seg.words or []):
                text = w.word.strip()
                if not text:
                    continue
                words.append(Word(
                    text=text,
                    start_ms=int(round(w.start * 1000)),
                    end_ms=int(round(w.end * 1000)),
                    confidence=float(getattr(w, "probability", 1.0)),
                ))

        # faster-whisper has no diarization. Speaker labels stay empty rather
        # than being faked — a single fabricated speaker would silently break
        # the speaker-change rule in stage 4 and the speaker_priority constraint
        # in stage 7.
        return ASRResult(
            words=words,
            language=info.language or (language or "en"),
            provider=self.name,
            model=str(self.model_name),
        )
