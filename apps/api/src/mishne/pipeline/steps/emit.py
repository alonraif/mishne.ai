"""Stage 11. OTIO to AAF, FCPXML, EDL. See docs/architecture/02 for the relink
problem and the AAF writer's known limitations.
"""

from .base import Step, StepContext


class EmitStep(Step):
    name = "emit"
    label = "Generate artifacts"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("emit step is not implemented yet")
