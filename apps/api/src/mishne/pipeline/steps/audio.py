"""Stage 1. ffmpeg -vn -ac 1 -ar 16000, per source clip, never on a flattened mix.
"""

from .base import Step, StepContext


class AudioStep(Step):
    name = "audio"
    label = "Extract audio"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("audio step is not implemented yet")
