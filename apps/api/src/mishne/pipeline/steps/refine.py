"""Stage 9. Deterministic. Snap outward to silence, add handles, never cut inside
a word, quantize to frame boundaries, merge near-adjacent selections.
"""

from .base import Step, StepContext


class RefineStep(Step):
    name = "refine"
    label = "Refine cut points"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("refine step is not implemented yet")
