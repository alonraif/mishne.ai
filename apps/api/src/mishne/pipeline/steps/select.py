"""Stage 7. Deterministic, OR-Tools CP-SAT. NOT an LLM — models cannot hit a
duration target. See docs/adr/0004-constraint-solver-for-selection.md.
"""

from .base import Step, StepContext


class SelectStep(Step):
    name = "select"
    label = "Solve selection"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("select step is not implemented yet")
