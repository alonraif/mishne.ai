"""Stage 5. LLM. Free-text director's notes to a strict EditBrief. Underspecified
notes get documented defaults, and every assumption goes in clarifications.
"""

from .base import Step, StepContext


class BriefStep(Step):
    name = "brief"
    label = "Compile edit brief"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("brief step is not implemented yet")
