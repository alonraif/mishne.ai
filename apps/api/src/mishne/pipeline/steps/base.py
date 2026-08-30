"""The step contract. One of them, and this is it.

A step is a unit of work the orchestrator can start, retry, resume and report
on. The rules it has to obey are the ones a distributed runner depends on:

**A step reads a reference and writes a reference.** Never a payload. Step
Functions caps its state at 256 KB and a transcript is larger than that on a
short interview, so what moves between steps is a key, and what a key names
lives in object storage (or, on one machine, in the work directory — see
`mishne.workspace`).

**A step is idempotent on `(job_id, name, attempt)`.** Spot interruption and
orchestrator retries both re-run steps that may have partly succeeded. A step
that appends, increments, or charges is a step that does the wrong thing the
second time.

**A step never logs customer content.** See `mishne.logging`.

**A payload carries a version.** In-flight jobs survive deploys (ADR-0012), so
a step written by the previous release is read by the next one. `PAYLOAD_VERSION`
is stored on every `job_steps` row; nothing may assume it is the current one.

## Why these are specs and not classes

The twelve working stages are plain functions, and they were plain functions
before any of this existed. An earlier sketch here was an ABC that exactly one
stage — an unimplemented one — subclassed, which left the codebase with two step
contracts describing the same pipeline and a registry that matched neither.
A `StepSpec` describes a stage; `mishne.orchestration.graph` binds each name to
the function that runs it; and a test asserts the two lists are the same list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: The shape of what a step reads and writes. Bump it when that shape changes,
#: and keep reading the previous one for at least one release (ADR-0012).
PAYLOAD_VERSION = 1

#: `asset` steps run once per upload and are cached forever — re-running a job
#: with an unchanged asset must never re-transcribe (ADR-0008), which is the
#: economics of the whole multi-upload feature. `job` steps run once across the
#: assets a job draws on.
Phase = Literal["asset", "job"]


@dataclass(frozen=True)
class StepContext:
    """What a step is given. Everything else it needs, it reads from a ref."""

    job_id: str
    org_id: str
    project_id: str
    #: 1 on the first run. A step that behaves differently on a retry — a model
    #: call that falls back to something cheaper, say — reads this.
    attempt: int = 1
    #: Progress, reported as it happens. Ends up in `job_steps.detail`, so it is
    #: counts and durations only, never a filename or a line of transcript.
    on_progress: object = None


@dataclass(frozen=True)
class StepSpec:
    """One stage of the pipeline, as the orchestrator sees it."""

    name: str
    label: str
    phase: Phase
    #: The coarse job status while this step is running. The progress UI shows
    #: the step; the job list shows this.
    status: str
    #: How many times to retry before failing the job. Zero is a deliberate
    #: statement: assembly or validation failing means something is genuinely
    #: wrong, and retrying it burns money to reach the same answer.
    retries: int = 0
    #: Same inputs, identical outputs — byte for byte. What makes a re-run
    #: verifiable, and what stages 8-12 promise.
    deterministic: bool = True
    #: Runs only for some assets. `prepare` and `audio` take a different path
    #: for an AAF (structured storage, embedded essence) than for a media file,
    #: and that branch is inside the step rather than a state of its own.
    branches: tuple[str, ...] = ()
