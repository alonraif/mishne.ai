"""Stage 0. Establish the time base: edit rate as a rational, start timecode,
drop-frame flag, audio sample rate. Every timing bug downstream traces back
to getting this wrong.
"""

from .base import Step, StepContext


class PrepareStep(Step):
    name = "prepare"
    label = "Probe and normalize"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("prepare step is not implemented yet")
