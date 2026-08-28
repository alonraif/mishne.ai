"""Stage 3. Silero VAD per track. Speech/silence intervals and breath positions.
This is what makes cut points sound natural.
"""

from .base import Step, StepContext


class VadStep(Step):
    name = "vad"
    label = "Build silence map"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("vad step is not implemented yet")
