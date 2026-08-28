"""Stage 12. Re-parse every generated file and diff against the canonical OTIO.
Mismatch fails the job — shipping a subtly wrong AAF costs more trust than
failing loudly.
"""

from .base import Step, StepContext


class ValidateStep(Step):
    name = "validate"
    label = "Validate round-trip"

    def run(self, ctx: StepContext, input_ref: str) -> str:
        raise NotImplementedError("validate step is not implemented yet")
