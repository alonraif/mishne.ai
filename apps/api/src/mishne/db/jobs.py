"""The job write path: submission, progress, and money.

Three things here are load-bearing and each is enforced somewhere other than
good intentions.

**Money moves through the ledger only** (ADR-0006). The balance is a projection
of an append-only table, never a mutable column somebody can nudge: hold at
submission, settle at completion for `min(actual, approved_cap)`, refund in full
on failure or cancellation. `org_balances` is written in the same transaction as
the ledger row, and it is reconstructible by summing `delta` — which is what
makes "why is my balance 142.5?" a query rather than an investigation.

**A retried settle is a duplicate-key error, not a double charge.** The unique
index on `(job_id, kind)` is the idempotency, so a worker that dies between
settling and marking the job complete can be re-run without charging twice.

**A step row is written before its step runs.** Progress that is written
afterwards is progress that never appears for the step that hung, which is the
one you actually want to see.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..pipeline.steps import PAYLOAD_VERSION, STEPS_BY_NAME
from . import models as m


class InsufficientCredits(Exception):
    def __init__(self, required: float, available: float) -> None:
        self.required = required
        self.available = available
        super().__init__(f"requires {required} credits, {available} available")


# ─────────────────────────────────────────────────────────────────── the job


def create_job(
    s: Session,
    org_id: str,
    *,
    project_id: str,
    asset_ids: list[str],
    mode: str,
    notes: str,
    brief: dict,
    estimate: dict,
    approved_cap: float,
) -> str:
    """The job row, its assets, and the hold — one transaction or none of it."""
    job_id = f"job_{secrets.token_hex(4)}"
    s.execute(
        sa.insert(m.Job.__table__).values(
            id=job_id,
            org_id=org_id,
            project_id=project_id,
            mode=mode,
            status="queued",
            notes_raw=notes,
            brief=brief,
            estimate=estimate,
            approved_cap=approved_cap,
        )
    )
    for idx, asset_id in enumerate(asset_ids):
        s.execute(
            sa.insert(m.JobAsset.__table__).values(
                org_id=org_id, job_id=job_id, asset_id=asset_id, order_idx=idx
            )
        )
    return job_id


def plan_steps(s: Session, org_id: str, job_id: str, rows: list[tuple[int, str, str]]) -> None:
    """Write every step as `pending` the moment the job is accepted.

    So a queued job shows its shape rather than an empty panel — and so the UI
    never has to guess how many steps a job has from a registry it also has a
    copy of.
    """
    for idx, name, _asset_id in rows:
        spec = STEPS_BY_NAME[name]
        s.execute(
            sa.insert(m.JobStep.__table__).values(
                id=f"stp_{job_id}_{idx:02d}",
                org_id=org_id,
                job_id=job_id,
                idx=idx,
                name=spec.name,
                status="pending",
                payload_version=PAYLOAD_VERSION,
            )
        )


def set_status(s: Session, org_id: str, job_id: str, status: str, **values) -> None:
    jobs = m.Job.__table__
    s.execute(
        sa.update(jobs)
        .where(jobs.c.org_id == org_id, jobs.c.id == job_id)
        .values(status=status, **values)
    )


def get_status(s: Session, org_id: str, job_id: str) -> str | None:
    jobs = m.Job.__table__
    row = s.execute(
        sa.select(jobs.c.status).where(jobs.c.org_id == org_id, jobs.c.id == job_id)
    ).first()
    return row.status if row else None


def upsert_step(
    s: Session,
    org_id: str,
    job_id: str,
    idx: int,
    name: str,
    *,
    status: str,
    attempt: int = 1,
    detail: str | None = None,
    output_ref: str | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """Idempotent on `(job_id, idx)`. A retry updates the row it already wrote."""
    table = m.JobStep.__table__
    now = datetime.now(timezone.utc)
    values: dict = {
        "status": status,
        "attempt": attempt,
        "detail": detail,
        "payload_version": PAYLOAD_VERSION,
    }
    if output_ref is not None:
        values["output_ref"] = output_ref
    if started:
        values["started_at"] = now
    if finished:
        values["finished_at"] = now

    updated = s.execute(
        sa.update(table)
        .where(table.c.org_id == org_id, table.c.job_id == job_id, table.c.idx == idx)
        .values(**values)
    )
    if updated.rowcount:
        return
    s.execute(
        sa.insert(table).values(
            id=f"stp_{job_id}_{idx:02d}",
            org_id=org_id,
            job_id=job_id,
            idx=idx,
            name=name,
            **values,
        )
    )


# ──────────────────────────────────────────────────────────────── the ledger


def balance(s: Session, org_id: str) -> tuple[float, float]:
    table = m.OrgBalance.__table__
    row = s.execute(sa.select(table).where(table.c.org_id == org_id)).first()
    return (float(row.available), float(row.held)) if row else (0.0, 0.0)


def _entry(
    s: Session,
    org_id: str,
    kind: str,
    delta: float,
    *,
    job_id: str | None = None,
    project_id: str | None = None,
    description: str = "",
    available: float,
    held: float,
) -> None:
    """One ledger row and the projection it implies, together or not at all."""
    s.execute(
        sa.insert(m.CreditLedger.__table__).values(
            id=f"led_{secrets.token_hex(8)}",
            org_id=org_id,
            project_id=project_id,
            job_id=job_id,
            kind=kind,
            delta=delta,
            balance_after=available,
            description=description,
        )
    )
    balances = m.OrgBalance.__table__
    s.execute(
        sa.update(balances)
        .where(balances.c.org_id == org_id)
        .values(available=available, held=held, updated_at=datetime.now(timezone.utc))
    )


def hold(s: Session, org_id: str, job_id: str, project_id: str, cap: float) -> None:
    """Reserve credits at submission.

    Holding at submission rather than debiting at completion is what stops a
    user with five credits starting ten concurrent jobs.
    """
    available, held = balance(s, org_id)
    if available < cap:
        raise InsufficientCredits(cap, available)
    _entry(
        s, org_id, "hold", -cap, job_id=job_id, project_id=project_id,
        description="job submitted", available=available - cap, held=held + cap,
    )


def settle(s: Session, org_id: str, job_id: str, actual: float, cap: float) -> float:
    """Charge actual usage on success, capped at what the user approved.

    The approved estimate is a ceiling: nobody is billed more than they agreed
    to at submission, which is what makes consumption pricing tolerable.
    """
    charged = min(actual, cap)
    available, held = balance(s, org_id)
    try:
        with s.begin_nested():
            _entry(
                s, org_id, "settle", -charged, job_id=job_id,
                description="job complete",
                available=available + (cap - charged), held=held - cap,
            )
    except IntegrityError:
        # The unique index on (job_id, kind). A retried settle is a duplicate,
        # not a second charge.
        return charged
    jobs = m.Job.__table__
    s.execute(
        sa.update(jobs)
        .where(jobs.c.org_id == org_id, jobs.c.id == job_id)
        .values(credits_settled=charged)
    )
    return charged


def release(s: Session, org_id: str, job_id: str, cap: float, *, reason: str) -> None:
    """Return the whole hold. A job that failed or was cancelled is never charged."""
    available, held = balance(s, org_id)
    try:
        with s.begin_nested():
            _entry(
                s, org_id, "release", cap, job_id=job_id, description=reason,
                available=available + cap, held=max(0.0, held - cap),
            )
    except IntegrityError:
        return


def purchase(s: Session, org_id: str, credits: float, *, stripe_event_id: str = "") -> None:
    available, held = balance(s, org_id)
    s.execute(
        sa.insert(m.CreditLedger.__table__).values(
            id=f"led_{secrets.token_hex(8)}",
            org_id=org_id,
            kind="purchase",
            delta=credits,
            balance_after=available + credits,
            description="credit pack",
            stripe_event_id=stripe_event_id or None,
        )
    )
    balances = m.OrgBalance.__table__
    s.execute(
        sa.update(balances)
        .where(balances.c.org_id == org_id)
        .values(available=available + credits, held=held,
                updated_at=datetime.now(timezone.utc))
    )
