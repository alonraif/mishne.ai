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

from ..db import jobs as job_writes
from ..db.base import session_for_org
from ..logging import get_logger
from .runner import StepRun

log = get_logger(__name__)


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
        self._write(run, status="done", finished=True)

    def step_failed(self, run: StepRun, will_retry: bool) -> None:
        # A step that will be retried stays `active`: showing "failed" for
        # something the system is about to do again reads as a broken job.
        self._write(run, status="active" if will_retry else "failed",
                    finished=not will_retry)

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
               finished: bool = False) -> None:
        try:
            with session_for_org(self.org_id) as s:
                job_writes.upsert_step(
                    s, self.org_id, self.job_id, run.idx, run.name,
                    status=status, attempt=run.attempt, detail=run.detail or None,
                    started=started, finished=finished,
                )
        except Exception as exc:  # noqa: BLE001 - progress is not worth a job
            # A job that fails because its progress bar could not be updated is
            # a worse outcome than a progress bar that stops moving.
            log.warning("progress.write_failed", job_id=self.job_id,
                        step=run.name, reason=type(exc).__name__)
