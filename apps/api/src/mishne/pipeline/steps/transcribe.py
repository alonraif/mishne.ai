"""Stage 2. Word-level timestamps, diarization on, smart formatting OFF —
disfluencies must be preserved because removing them is our job. Persist the
raw vendor response so reprocessing never re-pays for transcription.
"""

from .base import Step, StepContext


class TranscribeStep(Step):
    name = "transcribe"
    label = "Transcribe with word timestamps"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("transcribe step is not implemented yet")
