"""The runner: twelve stages, durably, on a machine that may die halfway.

`run.py` calls the stages in sequence in one process and prints as it goes. This
does the same work, in the same order, while answering three questions that a
script does not have to:

**Where is it up to?** Every step is recorded before it starts and after it
ends, so the progress UI shows real state rather than a spinner, and an operator
can tell a slow transcription from a wedged one.

**What happens when the worker dies?** The job is re-entered from the top and
every completed expensive stage is served from cache: the per-asset phase from
the content-addressed ingest cache (ADR-0008), and the three model-calling
stages from the job's own working directory. Resume is therefore *idempotent
re-execution*, not a checkpoint restore — which is the only kind of resume worth
having when the alternative is serialising a timeline and a solver's state
between two releases that may not agree about their shape.

**What does a retry cost?** Per stage, and deliberately asymmetric. Transcription
and the model stages are retried, because a provider returning 503 is not a
reason to fail somebody's job. Assembly and validation are not: a validation
failure means an artifact is wrong, and writing it again produces the same wrong
artifact while charging for the privilege.

Cancellation is checked between steps rather than inside them. A stage is at
most a few minutes and killing one mid-write is how a half-written artifact
reaches a customer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ..logging import get_logger
from ..pipeline import project
from ..pipeline.steps import ASSET_STEPS, JOB_STEPS, STEPS, StepSpec
from ..pipeline.steps.base import PAYLOAD_VERSION, StepContext
from . import graph
from .graph import AssetRun, JobRequest, RunState

log = get_logger(__name__)


class Cancelled(Exception):
    """The job was cancelled between steps. Not a failure — nothing is charged."""


@dataclass
class StepRun:
    """One execution of one step, as the runner reports it."""

    idx: int
    name: str
    label: str
    #: The asset this step ran for, empty for a job-phase step.
    asset_id: str = ""
    attempt: int = 1
    status: str = "pending"
    detail: str = ""
    error: str = ""
    seconds: float = 0.0


class ProgressSink(Protocol):
    """Where progress goes. The database in production, a list in a test."""

    def step_started(self, run: StepRun) -> None: ...
    def step_progress(self, run: StepRun, detail: str) -> None: ...
    def step_finished(self, run: StepRun) -> None: ...
    def step_failed(self, run: StepRun, will_retry: bool) -> None: ...
    def job_status(self, status: str) -> None: ...
    def cancelled(self) -> bool:
        """Checked between steps. A cancel is a user pressing a button, not an error."""


@dataclass
class RecordingSink:
    """Keeps everything in memory. What the tests assert against."""

    steps: list[StepRun] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    cancel_after: int | None = None

    def step_started(self, run: StepRun) -> None:
        self.steps.append(run)

    def step_progress(self, run: StepRun, detail: str) -> None:
        run.detail = detail

    def step_finished(self, run: StepRun) -> None:
        run.status = "done"

    def step_failed(self, run: StepRun, will_retry: bool) -> None:
        run.status = "failed"

    def job_status(self, status: str) -> None:
        if not self.statuses or self.statuses[-1] != status:
            self.statuses.append(status)

    def cancelled(self) -> bool:
        return (
            self.cancel_after is not None
            and sum(1 for s in self.steps if s.status == "done") >= self.cancel_after
        )


@dataclass
class JobResult:
    state: RunState
    steps: list[StepRun]
    seconds: float

    @property
    def artifacts(self) -> list:
        return self.state.artifacts


#: How long to wait before a retry. Short, and not configurable per job: a
#: transcription retry that waits a minute is a job that looks wedged, and a
#: retry that waits nothing hammers a provider that is already struggling.
RETRY_BACKOFF_S = (2, 8, 20)


def run_job(
    request: JobRequest,
    sink: ProgressSink | None = None,
    *,
    sleep=time.sleep,
) -> JobResult:
    """Execute one job to completion, or raise.

    Raises `Cancelled` when the job was cancelled between steps, and whatever a
    step raised when it has run out of retries. Both are the caller's to record
    against the ledger — this function does not touch money.
    """
    sink = sink or RecordingSink()
    state = RunState(request=request)
    started = time.time()
    steps: list[StepRun] = []
    idx = 0

    # ── the per-asset phase, once per upload ───────────────────────────────
    for source in request.assets:
        adir = _asset_dir(request, source.pipeline_id)
        run = AssetRun(source=source, adir=adir)
        # The cache read comes first and covers all six stages. This is what
        # "re-running a job with an unchanged asset performs zero
        # transcription" means, and it is the economics of multi-upload.
        run.ingest = project.cached_ingest(adir, source.path)
        run.from_cache = run.ingest is not None
        state.runs[source.asset_id] = run
        state.current = source.asset_id

        for spec in ASSET_STEPS:
            idx += 1
            steps.append(_execute(spec, idx, state, sink, sleep, asset_id=source.asset_id))

    # ── the per-job phase ──────────────────────────────────────────────────
    for spec in JOB_STEPS:
        idx += 1
        steps.append(_execute(spec, idx, state, sink, sleep))

    log.info(
        "job.complete",
        job_id=request.job_id,
        assets=len(request.assets),
        steps=len(steps),
        cached_assets=sum(1 for r in state.runs.values() if r.from_cache),
        seconds=round(time.time() - started, 1),
    )
    return JobResult(state=state, steps=steps, seconds=time.time() - started)


def _asset_dir(request: JobRequest, asset_id: str) -> Path:
    work = request.work_dir
    if hasattr(work, "asset_dir"):  # a Workspace, on a worker
        return work.asset_dir(asset_id)
    d = Path(work) / "assets" / asset_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _execute(
    spec: StepSpec,
    idx: int,
    state: RunState,
    sink: ProgressSink,
    sleep,
    asset_id: str = "",
) -> StepRun:
    """One step, with its retry policy. Cancellation is checked before it starts."""
    if sink.cancelled():
        raise Cancelled(f"cancelled before {spec.name}")

    run = StepRun(idx=idx, name=spec.name, label=spec.label, asset_id=asset_id)
    sink.job_status(spec.status)
    # Looked up at call time, through the module. A name bound at import is a
    # name a test cannot replace and a plugin cannot extend — and the point of
    # having one registry is that the binding is data, not an import.
    implementation = graph.IMPLEMENTATIONS[spec.name]

    for attempt in range(1, spec.retries + 2):
        run.attempt = attempt
        run.status = "active"
        started = time.time()
        sink.step_started(run)
        ctx = StepContext(
            job_id=state.request.job_id,
            org_id=state.request.org_id,
            project_id=state.request.project_id,
            attempt=attempt,
            on_progress=lambda detail, r=run: sink.step_progress(r, str(detail)),
        )
        try:
            detail = implementation(ctx, state) or ""
        except Exception as exc:  # noqa: BLE001 - the runner decides what is fatal
            run.seconds = time.time() - started
            run.error = f"{type(exc).__name__}: {exc}"
            will_retry = attempt <= spec.retries
            sink.step_failed(run, will_retry)
            log.warning(
                "step.failed",
                job_id=state.request.job_id,
                step=spec.name,
                attempt=attempt,
                will_retry=will_retry,
                # The type, never the message: a step's exception can quote a
                # filename, and a filename is customer content.
                reason=type(exc).__name__,
            )
            if not will_retry:
                raise
            sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
            continue

        run.seconds = time.time() - started
        run.detail = detail or run.detail
        run.status = "done"
        sink.step_finished(run)
        log.info(
            "step.done",
            job_id=state.request.job_id,
            step=spec.name,
            asset_id=asset_id or None,
            seconds=round(run.seconds, 2),
            attempt=attempt,
            payload_version=PAYLOAD_VERSION,
        )
        return run

    raise RuntimeError(f"unreachable: {spec.name} neither succeeded nor failed")


def plan(request: JobRequest) -> list[tuple[int, str, str]]:
    """What this job will run, before it runs: (idx, step, asset or empty).

    The rows the UI needs the moment a job is accepted, so a job that has not
    started yet still shows its shape rather than an empty panel.
    """
    rows: list[tuple[int, str, str]] = []
    idx = 0
    for source in request.assets:
        for spec in ASSET_STEPS:
            idx += 1
            rows.append((idx, spec.name, source.asset_id))
    for spec in JOB_STEPS:
        idx += 1
        rows.append((idx, spec.name, ""))
    return rows


__all__ = [
    "STEPS",
    "Cancelled",
    "JobResult",
    "ProgressSink",
    "RecordingSink",
    "StepRun",
    "plan",
    "run_job",
]
