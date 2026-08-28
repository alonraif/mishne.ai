"""Stage 8. LLM. Coherence pass over the assembled selection. Returns targeted
operations fed back as solver constraints. Bounded at two iterations.
"""

from .base import Step, StepContext


class ReviewStep(Step):
    name = "review"
    label = "Review sequence"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("review step is not implemented yet")
