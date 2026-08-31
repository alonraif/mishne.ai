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
from ..telemetry import span
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
    #: The attempt that ended the step.
    seconds: float = 0.0
    #: Every attempt, failed ones included. Equal to `seconds` on a step that
    #: succeeded first time; the difference is what the retries cost.
    cumulative_seconds: float = 0.0
    #: The per-asset phase was served from the ingest cache rather than executed
    #: (ADR-0016). A stage that took no time because it did no work has to say
    #: so, or a cost baseline averages the cache hits into the work.
    from_cache: bool = False
    #: The model calls this step made, as `llm.base.CallRecord`s. The router's
    #: ledger is per-job and knows nothing about stages; sliced here, where the
    #: step boundary is, it becomes cost per stage.
    llm_calls: list = field(default_factory=list)


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
    #: The step this run deliberately stopped after, empty when it ran to the
    #: end. A paused job is not finished and is not failed: it is waiting for a
    #: person, its hold stands, and nothing is settled.
    paused_after: str = ""

    @property
    def artifacts(self) -> list:
        return self.state.artifacts

    @property
    def paused(self) -> bool:
        return bool(self.paused_after)


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

    # One span for the job, every stage nested inside it. `job_id` is what
    # correlates the two, and it is the id a support conversation starts from.
    job_span = span(
        "job",
        job_id=request.job_id,
        org_id=request.org_id,
        project_id=request.project_id,
        assets=len(request.assets),
        mode=request.mode,
    )

    with job_span as job_trace:
        paused_after = _run_phases(request, state, sink, sleep, steps)
        cached = sum(1 for r in state.runs.values() if r.from_cache)
        job_trace.set(
            steps=len(steps),
            cached_assets=cached,
            paused_after=paused_after,
            seconds=round(time.time() - started, 3),
        )

    log.info(
        "job.paused" if paused_after else "job.complete",
        job_id=request.job_id,
        assets=len(request.assets),
        steps=len(steps),
        paused_after=paused_after,
        cached_assets=sum(1 for r in state.runs.values() if r.from_cache),
        seconds=round(time.time() - started, 1),
    )
    return JobResult(state=state, steps=steps, seconds=time.time() - started,
                     paused_after=paused_after)


#: What each mode runs, and where it stops.
#:
#: `manual` and `hybrid` have existed in the schema, the API and the UI since
#: B1, and the orchestrator ignored `mode` entirely — every job ran straight
#: through to an artifact, so no job ever reached `awaiting_edit` and the cut
#: editor had nothing to open. This is that gap.
#:
#:   ai      everything, once.
#:   manual  transcribe, compile the brief, stop. The person marks the cut on
#:           the text; proposing and scoring candidates for a solver that will
#:           never run is money spent on nothing.
#:   hybrid  everything through `refine`, then stop with a suggestion loaded.
#:           Refined rather than raw: the suggestion an editor judges should be
#:           the cut they would get, silence-snapped and frame-accurate, not the
#:           solver's intention before stage 9 touched it.
#:
#: A run carrying a user's cut skips proposing and scoring in every mode —
#: those exist to feed the solver, and the solver has been replaced by a person
#: (`graph.step_select`).
def phases_for(mode: str, user_cut: list[str] | None = None
               ) -> tuple[frozenset[str], str]:
    """(steps to skip, step to stop after) for one run of a job."""
    if user_cut:
        return frozenset({"propose", "score"}), ""
    if mode == "manual":
        return frozenset({"propose", "score", "select"}), "brief"
    if mode == "hybrid":
        return frozenset(), "refine"
    return frozenset(), ""


def _run_phases(request, state, sink, sleep, steps: list) -> str:
    """The per-asset phase then the per-job phase, in the registry's order.

    Returns the step it stopped after, or "" if it ran to the end.

    `idx` counts every step in the registry whether or not this run executes it,
    because it is the key `job_steps` rows were planned under
    (`runner.plan`). Renumbering around a skipped step writes each result to
    the row of the step before it, and the progress panel then shows a job
    whose stages are all one place out.
    """
    skip, pause_after = phases_for(request.mode, request.user_cut)
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
        if spec.name in skip:
            continue
        steps.append(_execute(spec, idx, state, sink, sleep))
        if spec.name == pause_after:
            return spec.name
    return ""


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
    # A per-asset step whose whole phase came from cache did not do the work its
    # duration would otherwise imply.
    if asset_id and asset_id in state.runs:
        run.from_cache = state.runs[asset_id].from_cache
    # Where the job's model spend stood before this step. The ledger is a flat
    # list per job, so the calls this step is responsible for are the ones
    # appended while it ran.
    ledger = getattr(state.request.router, "ledger", None)
    calls_before = len(ledger.calls) if ledger else 0
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
        # A span per ATTEMPT rather than per step. A step that was retried is
        # two pieces of work that took different times for different reasons,
        # and averaging them into one span is how the retry disappears.
        with span(
            f"step.{spec.name}",
            job_id=state.request.job_id,
            org_id=state.request.org_id,
            step=spec.name,
            step_idx=idx,
            asset_id=asset_id or None,
            attempt=attempt,
            # Why a re-run is fast. Without it the cache hit looks like the
            # work vanished, and a stage that did nothing in 40ms is
            # indistinguishable from one that broke silently.
            from_cache=run.from_cache,
        ) as trace:
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
                run.cumulative_seconds += run.seconds
                if ledger:
                    run.llm_calls = ledger.calls[calls_before:]
                run.error = f"{type(exc).__name__}: {exc}"
                will_retry = attempt <= spec.retries
                # Marked here rather than left to the context manager: an
                # attempt that will be retried does not propagate, so the
                # `with` block exits cleanly and a failure that the system
                # recovered from would show as a successful span. By exception
                # TYPE — never `record_exception`, which attaches the message
                # and the stack trace.
                trace.failed(exc)
                trace.set(will_retry=will_retry, seconds=round(run.seconds, 3))
                sink.step_failed(run, will_retry)
                log.warning(
                    "step.failed",
                    job_id=state.request.job_id,
                    step=spec.name,
                    attempt=attempt,
                    will_retry=will_retry,
                    # The type, never the message: a step's exception can quote
                    # a filename, and a filename is customer content.
                    reason=type(exc).__name__,
                )
                if not will_retry:
                    raise
                _retried = True
            else:
                _retried = False
                run.seconds = time.time() - started
                run.cumulative_seconds += run.seconds
                if ledger:
                    # Every attempt's calls, not just the successful one's: a
                    # step that was retried spent real money on the attempts
                    # that failed, and a cost record that omits them
                    # under-reports exactly the jobs that went wrong.
                    run.llm_calls = ledger.calls[calls_before:]
                trace.set(
                    seconds=round(run.seconds, 3),
                    llm_calls=len(run.llm_calls),
                    llm_cost_usd=round(
                        sum(c.cost_usd for c in run.llm_calls), 6
                    ) or None,
                )

        if _retried:
            sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
            continue

        run.detail = detail or run.detail
        run.status = "done"
        sink.step_finished(run)
        log.info(
            "step.done",
            job_id=state.request.job_id,
            step=spec.name,
            asset_id=asset_id or None,
            seconds=round(run.seconds, 2),
            cumulative_seconds=round(run.cumulative_seconds, 2),
            from_cache=run.from_cache,
            llm_calls=len(run.llm_calls),
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
    "phases_for",
    "Cancelled",
    "JobResult",
    "ProgressSink",
    "RecordingSink",
    "StepRun",
    "plan",
    "run_job",
]
