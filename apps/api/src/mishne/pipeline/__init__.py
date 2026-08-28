"""The edit pipeline.

Twelve stages, three of which call an LLM. Every step is a pure, idempotent
function of (job_id, step_input_ref) -> step_output_ref, with payloads in object
storage and status in Postgres. That contract is what keeps the orchestrator
swappable — see docs/adr/0002-workflow-engine-not-agent-framework.md.

Do not introduce an agent loop here.
"""

from .steps import STEPS

__all__ = ["STEPS"]
