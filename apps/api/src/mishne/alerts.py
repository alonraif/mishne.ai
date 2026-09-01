"""Alerting on what actually hurts.

Four conditions, and the discipline is in what is *not* here. An alert that
fires on something the system already handled trains its reader to close it
without looking, and a monitor nobody reads is worse than no monitor because it
is mistaken for one.

## The four

| Condition | Why it hurts |
|---|---|
| A job failed after its retries | A customer is waiting for a deliverable that is not coming |
| A step left its own duration distribution | A wedged transcription looks exactly like a slow one until you compare it to the others |
| Spend per job moved | A routing change or a vendor price change reaches the bill before it reaches anyone's attention |
| The queue is growing | Arrivals are outpacing throughput, which is the failure that gets worse while you look at it |

## And what is deliberately not one

**A step that retried and then succeeded.** The runner retries transcription and
the model stages precisely because a provider returning 503 is not a reason to
fail somebody's job (B3). Paging on that is paging on the system working.

**A failover.** `llm/router.py` moves across vendors mid-job. That is one call
that succeeded, not two of which one failed, and counting it the other way
gives an error rate wrong in the direction that causes needless work — which is
why `fell_back_from` exists on the call record and is checked here.

## The duration rule, and why it is a multiple rather than a percentile

A p95 alert on a stage whose duration is dominated by the length of the material
fires every time somebody uploads a long interview. The question worth asking is
not "is this in the slowest 5%" but "is this unlike what this stage does", and
for transcription the honest threshold is loose: several times the median, not a
tight bound. Below `alert_duration_min_samples` prior runs there is no
distribution to leave, and comparing against three samples produces confident
nonsense — so it does not fire at all.

## Delivery

Emitting is structured logging, at `warning` for something to look at and
`error` for something to wake up for. Where those go — a pager, a channel, an
inbox — is a deployment decision, the same way the trace exporter is, and
nothing here names a product.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlalchemy as sa

from .config import get_settings
from .db import models as m
from .logging import get_logger

log = get_logger(__name__)

#: `page` wakes somebody. `notice` is looked at during the day. The distinction
#: is the whole value of the module and belongs in the data, not in a comment.
PAGE = "page"
NOTICE = "notice"


@dataclass
class Alert:
    name: str
    severity: str
    #: IDs, counts and durations only — this is emitted, and the content rule
    #: applies to an alert exactly as it applies to a log line.
    facts: dict = field(default_factory=dict)

    def emit(self) -> None:
        emitter = log.error if self.severity == PAGE else log.warning
        emitter(f"alert.{self.name}", severity=self.severity, **self.facts)


# ── a job that failed for good ────────────────────────────────────────────


def job_failed(job_id: str, *, step: str, reason: str, attempts: int,
               status: int = 0) -> Alert:
    """A job that ran out of retries. Somebody is waiting for a deliverable.

    Called only from the worker's terminal failure path. A step that failed and
    was retried never reaches here — `runner._execute` raises only when the
    retries are exhausted, which is the same boundary a human would draw.
    """
    return Alert(
        "job.failed",
        PAGE,
        # `reason` is the exception TYPE. The message is not carried, here or
        # anywhere else (docs/architecture/04-security.md) — but `status`, a
        # vendor's HTTP code, is a fact about our request rather than about the
        # customer's material, and it is what says whether the answer is to
        # wait or to fix something.
        {"job_id": job_id, "step": step, "reason": reason, "attempts": attempts,
         **({"status": status} if status else {})},
    )


# ── a step that left its distribution ─────────────────────────────────────


def slow_step(
    s,
    org_id: str,
    job_id: str,
    *,
    step: str,
    seconds: float,
    settings=None,
) -> Alert | None:
    """Compare this run of a stage against every previous run of the same stage.

    Returns None when there is nothing to say, which is most of the time.
    """
    settings = settings or get_settings()
    steps = m.JobStep.__table__
    row = s.execute(
        sa.select(
            sa.func.count(steps.c.seconds),
            sa.func.percentile_cont(0.5).within_group(steps.c.seconds.asc()),
        ).where(
            steps.c.org_id == org_id,
            steps.c.name == step,
            steps.c.status == "done",
            steps.c.seconds.is_not(None),
            # A cache hit took no time because it did no work. Averaging those
            # into the median drags it toward zero and makes every real
            # execution look like an outlier.
            steps.c.from_cache.is_(False),
            steps.c.job_id != job_id,
        )
    ).one()
    samples, median = row[0], row[1]

    if samples < settings.alert_duration_min_samples or not median:
        return None
    if seconds < median * settings.alert_duration_multiple:
        return None
    return Alert(
        "step.slow",
        NOTICE,
        {
            "job_id": job_id,
            "step": step,
            "seconds": round(seconds, 1),
            "median_seconds": round(float(median), 1),
            "samples": samples,
        },
    )


# ── spend that moved ──────────────────────────────────────────────────────


def spend_moved(
    s, org_id: str, job_id: str, *, cost_cents: int, tolerance: float = 2.0
) -> Alert | None:
    """This job's model spend against the recent median for the org.

    The condition this is really watching for is a routing or pricing change
    that reaches the bill before it reaches anybody's attention: a vendor
    repricing a model, or a policy change quietly promoting every task to a
    frontier model.
    """
    jobs = m.Job.__table__
    recent = sa.select(jobs.c.cost_cents).where(
        jobs.c.org_id == org_id,
        jobs.c.status == "complete",
        jobs.c.cost_cents > 0,
        jobs.c.id != job_id,
    ).order_by(jobs.c.created_at.desc()).limit(50).subquery()
    median = s.execute(
        sa.select(
            sa.func.percentile_cont(0.5).within_group(recent.c.cost_cents.asc())
        )
    ).scalar()

    if not median or cost_cents <= median * tolerance:
        return None
    return Alert(
        "spend.moved",
        NOTICE,
        {
            "job_id": job_id,
            "cost_cents": cost_cents,
            "median_cost_cents": int(median),
        },
    )


# ── a balance that will not cover the next job ────────────────────────────


def low_balance(
    s, org_id: str, *, available: float, settings=None
) -> Alert | None:
    """Warn before the customer hits a 402, not after.

    A job refused for want of credits is the worst moment to discover the
    balance: the material is uploaded, the brief is written, and the person is
    ready to work. `POST /v1/jobs` already returns a 402 with the shortfall —
    this is the part that means they never see it.

    **Scaled to their own jobs, not to a constant.** "Under 10 credits" is
    trivial for a broadcaster cutting twelve-hour days and a permanent nag for
    somebody who cuts one short a month. So the threshold is a multiple of what
    this org's recent jobs have actually cost, with a flat floor for an org that
    has not run one yet.

    Returns None when the balance is fine, which is the common case.
    """
    from .config import get_settings

    settings = settings or get_settings()
    jobs = m.Job.__table__
    recent = sa.select(jobs.c.credits_settled).where(
        jobs.c.org_id == org_id,
        jobs.c.status == "complete",
        jobs.c.credits_settled > 0,
    ).order_by(jobs.c.created_at.desc()).limit(10).subquery()
    typical = s.execute(
        sa.select(
            sa.func.percentile_cont(0.5).within_group(
                recent.c.credits_settled.asc()
            )
        )
    ).scalar()

    threshold = (
        float(typical) * settings.low_balance_jobs
        if typical
        else settings.low_balance_floor
    )
    if available >= threshold:
        return None
    return Alert(
        "balance.low",
        NOTICE,
        {
            "org_id": org_id,
            "available": round(available, 2),
            "threshold": round(threshold, 2),
            "typical_job": round(float(typical), 2) if typical else None,
        },
    )


# ── a queue that is growing ───────────────────────────────────────────────


def queue_growing(s, *, threshold: int = 20, oldest_minutes: int = 30) -> Alert | None:
    """Depth alone is not the signal; depth plus age is.

    Twenty queued jobs that arrived a minute ago is a busy morning. Twenty
    queued jobs of which the oldest has been waiting half an hour means nothing
    is draining them, and that is the failure that gets worse while you look at
    it.

    Deliberately not scoped to one org: this is a question about the platform's
    throughput, and it is asked by an operator rather than served to a customer.
    """
    jobs = m.Job.__table__
    depth, oldest = s.execute(
        sa.select(
            sa.func.count(jobs.c.id),
            sa.func.min(jobs.c.created_at),
        ).where(jobs.c.status == "queued")
    ).one()

    if depth < threshold or oldest is None:
        return None
    age_minutes = s.execute(
        sa.select(sa.func.extract("epoch", sa.func.now() - oldest) / 60)
    ).scalar_one()
    if age_minutes < oldest_minutes:
        return None
    return Alert(
        "queue.growing",
        PAGE,
        {"depth": depth, "oldest_wait_minutes": int(age_minutes)},
    )
