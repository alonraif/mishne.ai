"""Progress, into the database the UI reads.

The runner does not know what a database is — it reports to a `ProgressSink`,
and this is the one that writes `jobs` and `job_steps`. Keeping it out of the
runner is what lets the same execution path run on a laptop with no Postgres
at all, which is how `run.py` and the tests use it.

Each write is its own short transaction. That is deliberate: progress is only
useful if it is visible while the job is still running, and a step that holds a
transaction open for the eight minutes it spends transcribing is a step that
also holds a connection, a lock, and any hope of seeing where the job is up to.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import alerts
from ..db import jobs as job_writes
from ..db.base import session_for_org
from ..logging import get_logger
from .runner import StepRun

log = get_logger(__name__)


def _error_facts(run: StepRun) -> dict:
    """What may be written about a failed attempt.

    The exception type and, when the vendor answered with one, its HTTP status.
    Never `run.error` itself: the message is the part that can quote a filename
    (docs/architecture/04-security). The status is what tells an operator
    whether to wait or to look at the request — job_ef028410 failed three times
    with `ASRError` and nothing else, and answering "which ASRError" took an
    afternoon and a reproduction.
    """
    facts: dict = {"code": run.error_code or "Error"}
    if run.error_status:
        facts["status"] = run.error_status
    return facts


class DatabaseSink:
    """Writes progress for one job, one short transaction at a time."""

    def __init__(self, org_id: str, job_id: str) -> None:
        self.org_id = org_id
        self.job_id = job_id
        self._status = ""

    # ── the runner's interface ─────────────────────────────────────────────

    def step_started(self, run: StepRun) -> None:
        self._write(run, status="active", started=True)

    def step_progress(self, run: StepRun, detail: str) -> None:
        # Counts and durations only. `job_steps.detail` is rendered in the UI
        # and stored for the life of the job, so the same rule applies as to
        # logs: no filenames, no transcript text (docs/architecture/04).
        run.detail = detail
        self._write(run, status="active")

    def step_finished(self, run: StepRun) -> None:
        # `error={}` clears what a failed earlier attempt of this step wrote.
        self._write(run, status="done", finished=True, costs=True, error={})
        self._check_duration(run)

    def step_failed(self, run: StepRun, will_retry: bool) -> None:
        # A step that will be retried stays `active`: showing "failed" for
        # something the system is about to do again reads as a broken job.
        # A failed attempt still spent money on the model calls it made before
        # it failed, so its costs are recorded whether or not it will be tried
        # again. `record_llm_calls` is keyed on the step, so the retry rewrites
        # these rows rather than adding to them.
        self._write(run, status="active" if will_retry else "failed",
                    finished=not will_retry, costs=True,
                    error=_error_facts(run))

    def job_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        with session_for_org(self.org_id) as s:
            values = {}
            if status == "preparing" and not self._started(s):
                values["started_at"] = datetime.now(timezone.utc)
            job_writes.set_status(s, self.org_id, self.job_id, status, **values)

    def cancelled(self) -> bool:
        """A cancel is a row, not a signal.

        The API marks the job cancelled and the runner notices between steps.
        No inter-process signalling, no partial writes: a stage is at most a few
        minutes, and killing one mid-write is how a half-written artifact
        reaches a customer.
        """
        with session_for_org(self.org_id) as s:
            return job_writes.get_status(s, self.org_id, self.job_id) == "cancelled"

    # ── internals ──────────────────────────────────────────────────────────

    def _check_duration(self, run: StepRun) -> None:
        """Was this stage unlike what this stage does?

        Asked after the row is written, so the comparison includes nothing about
        this run and the alert is never the reason the step's progress is late.
        A cache hit is skipped: it took no time because it did no work, and
        neither side of that comparison means anything.
        """
        if run.from_cache or not run.seconds:
            return
        try:
            with session_for_org(self.org_id) as s:
                alert = alerts.slow_step(
                    s, self.org_id, self.job_id, step=run.name,
                    seconds=run.seconds,
                )
            if alert:
                alert.emit()
        except Exception as exc:  # noqa: BLE001 - a monitor never fails a job
            log.warning("alert.check_failed", job_id=self.job_id,
                        step=run.name, reason=type(exc).__name__)


    def _started(self, s) -> bool:
        from sqlalchemy import select

        from ..db import models as m

        jobs = m.Job.__table__
        row = s.execute(
            select(jobs.c.started_at).where(
                jobs.c.org_id == self.org_id, jobs.c.id == self.job_id
            )
        ).first()
        return bool(row and row.started_at)

    def _write(self, run: StepRun, *, status: str, started: bool = False,
               finished: bool = False, costs: bool = False,
               error: dict | None = None) -> None:
        try:
            with session_for_org(self.org_id) as s:
                spend = None
                if costs:
                    # The calls first, then the step row that sums them: the
                    # step's cost column is a projection of those rows and must
                    # not be able to claim a figure they do not support.
                    spend = job_writes.record_llm_calls(
                        s, self.org_id, self.job_id, run.idx, run.name,
                        run.llm_calls,
                    )
                job_writes.upsert_step(
                    s, self.org_id, self.job_id, run.idx, run.name,
                    status=status, attempt=run.attempt, detail=run.detail or None,
                    asset_id=run.asset_id or None,
                    seconds=round(run.seconds, 3) if costs else None,
                    cumulative_seconds=(
                        round(run.cumulative_seconds, 3) if costs else None
                    ),
                    from_cache=run.from_cache or None,
                    model_cost_micros=spend,
                    error=error,
                    started=started, finished=finished,
                )
        except Exception as exc:  # noqa: BLE001 - progress is not worth a job
            # A job that fails because its progress bar could not be updated is
            # a worse outcome than a progress bar that stops moving.
            log.warning("progress.write_failed", job_id=self.job_id,
                        step=run.name, reason=type(exc).__name__)
