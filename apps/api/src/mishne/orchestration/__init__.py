"""Orchestration: the pipeline as a job that survives the worker running it.

`graph` binds the step registry to what runs each stage, `runner` executes them
durably with retries and cancellation, `statemachine` generates the Step
Functions definition from the same registry, and `sink` writes progress into the
database. `run.py` remains the specification for what the pipeline produces.

Imports here are lazy, and that is not tidiness. `graph` pulls in
OpenTimelineIO, ffmpeg bindings and a solver, and two things that have no
business needing them import from this package: the API process, which only
plans a job's steps, and `infra/` tooling that generates the state machine from
the registry. Neither should fail on a machine without the media stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .graph import AssetSource, JobRequest, RunState
    from .runner import Cancelled, JobResult, RecordingSink, StepRun, plan, run_job

_LAZY = {
    "AssetSource": ".graph",
    "JobRequest": ".graph",
    "RunState": ".graph",
    "Cancelled": ".runner",
    "JobResult": ".runner",
    "RecordingSink": ".runner",
    "StepRun": ".runner",
    "plan": ".runner",
    "run_job": ".runner",
}


def __getattr__(name: str):
    """PEP 562: resolve the heavy names only when something actually uses one."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)


__all__ = sorted(_LAZY)
