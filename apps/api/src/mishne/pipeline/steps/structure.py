"""Stage 4. Deterministic. Words to sentences, sentences to beats. Flag filler,
false starts, retakes, crosstalk, low confidence, off-mic. Flag, never delete.
"""

from .base import Step, StepContext


class StructureStep(Step):
    name = "structure"
    label = "Structure into beats"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("structure step is not implemented yet")
