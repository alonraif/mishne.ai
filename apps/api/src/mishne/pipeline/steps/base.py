"""Step contract.

A step reads its input from object storage, does one thing, writes its output to
object storage, and returns the reference. It must be safe to run twice with the
same input — spot interruption and orchestrator retries both depend on it.

Steps must never log customer content. See mishne.logging.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class StepContext:
    job_id: str
    org_id: str
    project_id: str
    attempt: int


class Step(ABC):
    name: str
    label: str

    @abstractmethod
    def run(self, ctx: StepContext, input_ref: str) -> str:
        """Run the step. Returns an object-storage reference to its output."""
        raise NotImplementedError
