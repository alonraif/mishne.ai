"""Stage 10. Build the canonical OTIO timeline. Everything else is a projection
of this document.
"""

from .base import Step, StepContext


class AssembleStep(Step):
    name = "assemble"
    label = "Assemble timeline"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("assemble step is not implemented yet")
