"""Stage 6. LLM. Per-beat scores plus depends_on and a one-line rationale.
depends_on is the field most often missed and the one that most affects
perceived quality.
"""

from .base import Step, StepContext


class ScoreStep(Step):
    name = "score"
    label = "Score beats"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("score step is not implemented yet")
