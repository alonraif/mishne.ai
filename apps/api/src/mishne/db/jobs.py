"""The job write path: submission, progress, and money.

Three things here are load-bearing and each is enforced somewhere other than
good intentions.

**Money moves through the ledger only** (ADR-0006). The balance is a projection
of an append-only table, never a mutable column somebody can nudge: hold at
submission, settle at completion for `min(actual, approved_cap)`, refund in full
on failure or cancellation. `org_balances` is written in the same transaction as
the ledger row, and it is reconstructible by summing `delta` — which is what
makes "why is my balance 142.5?" a query rather than an investigation.

**`delta` means one thing everywhere: the change in AVAILABLE credits.** That is
what makes the sum reconstruct the balance, and it is worth stating because
`settle` looks wrong until you hold it in mind:

    hold     -cap              the money becomes unavailable
    release  +cap              all of it comes back
    settle   +(cap - charged)  the UNUSED part comes back

A settle row is therefore positive, and what the customer was actually charged
is the hold and the settle together: `cap - (cap - charged) == charged`. It is
also on `jobs.credits_settled` and in the row's description.

This was wrong until C1. `settle` wrote `-charged` while its own `balance_after`
recorded the balance going *up* by `cap - charged` — so a row contradicted
itself, and summing deltas double-counted every completed job's hold. Nothing
caught it because the one ledger test asserted that a hold and its release net
to zero, which they did; nobody had summed a hold and a settle.

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


def replace_cut(
    s: Session,
    org_id: str,
    job_id: str,
    spans: list[tuple[str, str, int, int]],
) -> None:
    """This job's cut, as (asset_id, beat_id, in_frames, out_frames) in order.

    Replaces rather than merges: a person who removes a line and saves again
    means the line is gone, and an upsert would leave it there. The whole thing
    is one transaction, so a failed write leaves the previous cut intact rather
    than half of the new one.

    Beat ids are the caller's to validate — this function will happily store a
    cut of somebody else's beats, and `routers/jobs.submit_cut` is where that
    is checked, because it is the layer that knows whose job this is.
    """
    table = m.Selection.__table__
    s.execute(
        sa.delete(table).where(table.c.org_id == org_id, table.c.job_id == job_id)
    )
    for order_idx, (asset_id, beat_id, start, end) in enumerate(spans):
        s.execute(
            sa.insert(table).values(
                id=f"sel_{job_id}_{order_idx:03d}",
                org_id=org_id,
                job_id=job_id,
                beat_id=beat_id,
                asset_id=asset_id,
                order_idx=order_idx,
                src_tc_in_frames=start,
                src_tc_out_frames=end,
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
    asset_id: str | None = None,
    seconds: float | None = None,
    cumulative_seconds: float | None = None,
    from_cache: bool | None = None,
    model_cost_micros: int | None = None,
    started: bool = False,
    finished: bool = False,
) -> None:
    """Idempotent on `(job_id, idx)`. A retry updates the row it already wrote.

    Which is exactly why `seconds` is written rather than left to be derived
    from `finished_at - started_at`: the retry that overwrites this row also
    overwrites the timestamps, so the derived duration is the last attempt's
    and a stage that failed twice before succeeding reads as a cheap one.
    `cumulative_seconds` is every attempt, and the gap between the two is what
    a retry cost.
    """
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
    # None means "the caller has nothing to say about this field", which is not
    # the same as zero. A progress write mid-step must not blank the duration a
    # previous attempt recorded.
    for column, value in (
        ("asset_id", asset_id),
        ("seconds", seconds),
        ("cumulative_seconds", cumulative_seconds),
        ("from_cache", from_cache),
        ("model_cost_micros", model_cost_micros),
    ):
        if value is not None:
            values[column] = value
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


# ─────────────────────────────────────────────────────────────────── the cost


#: A dollar in micros. Model spend is recorded at this resolution because a
#: scoring call costs a fraction of a cent, and integer cents rounds a real
#: number to zero.
MICROS_PER_USD = 1_000_000


def record_llm_calls(
    s: Session,
    org_id: str,
    job_id: str,
    step_idx: int,
    step_name: str,
    calls: list,
) -> int:
    """One row per model call this step made. Returns the spend, in micros.

    `calls` are `llm.base.CallRecord`s, read field by field rather than through
    `to_dict()`: that method exists to shrink a manifest and drops empty values,
    and a column is not optional.

    Idempotent by construction — the id carries the step and the call's position
    within it, so re-running a step after a worker died rewrites its own rows
    instead of doubling the job's recorded spend.
    """
    table = m.JobLlmCall.__table__
    spend = 0
    for position, call in enumerate(calls):
        micros = int(round(call.cost_usd * MICROS_PER_USD))
        spend += micros
        values = {
            "org_id": org_id,
            "job_id": job_id,
            "step_idx": step_idx,
            "step_name": step_name,
            "task": call.task,
            "provider": call.provider,
            "model": call.model,
            "ok": call.ok,
            "latency_ms": call.latency_ms,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_micros": micros,
            "priced": getattr(call, "priced", True),
            "fell_back_from": call.fell_back_from,
            "violations": call.violations,
            "proposals": call.proposals,
            # `CallRecord.error` holds the exception TYPE, not a provider's
            # message, and this column is named for what it actually contains.
            "error_type": call.error,
            "audio_seconds": getattr(call, "audio_seconds", 0.0),
            "cost_estimated": getattr(call, "cost_estimated", False),
        }
        call_id = f"llm_{job_id}_{step_idx:02d}_{position:03d}"
        updated = s.execute(
            sa.update(table)
            .where(table.c.org_id == org_id, table.c.id == call_id)
            .values(**values)
        )
        if not updated.rowcount:
            s.execute(sa.insert(table).values(id=call_id, **values))
    return spend


def set_job_cost(
    s: Session, org_id: str, job_id: str, *, model_versions: dict
) -> int:
    """Project the job's recorded model spend onto the job row. Returns cents.

    `jobs.cost_cents` is a projection of `job_llm_calls`, in the same way
    `org_balances` is a projection of `credit_ledger`: the rows are the truth
    and this is the number a page renders. It is written from a SUM rather than
    from whatever the caller was holding, so a job resumed by a second worker
    reports what the database actually recorded rather than what one process
    happened to see.

    Cents lose the sub-cent detail on purpose — that is what the column is, and
    `job_llm_calls.cost_micros` is where the precision lives. Do not add a
    second cost figure to this row: two descriptions of one contract is how one
    of them stops being true.
    """
    calls = m.JobLlmCall.__table__
    micros = s.execute(
        sa.select(sa.func.coalesce(sa.func.sum(calls.c.cost_micros), 0)).where(
            calls.c.org_id == org_id, calls.c.job_id == job_id
        )
    ).scalar_one()
    cents = int(round(micros / 10_000))
    jobs = m.Job.__table__
    s.execute(
        sa.update(jobs)
        .where(jobs.c.org_id == org_id, jobs.c.id == job_id)
        .values(cost_cents=cents, model_versions=model_versions)
    )
    return cents


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


def _project_for_job(s: Session, org_id: str, job_id: str) -> str | None:
    """Which project a job belongs to.

    Looked up rather than passed in, because `settle` and `release` are called
    from the worker, which knows a job id and would otherwise have to carry a
    project id through the whole run purely so the ledger could record it.
    """
    jobs = m.Job.__table__
    return s.execute(
        sa.select(jobs.c.project_id).where(
            jobs.c.org_id == org_id, jobs.c.id == job_id
        )
    ).scalar()


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
                # The unused part of the hold coming back — see the note at the
                # top of this module on what `delta` means. The hold already
                # took the whole cap out of `available`; this returns whatever
                # the job did not use, and the two together are the charge.
                s, org_id, "settle", cap - charged, job_id=job_id,
                project_id=_project_for_job(s, org_id, job_id),
                description=f"job complete: charged {charged:g} of {cap:g}",
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
                s, org_id, "release", cap, job_id=job_id,
                project_id=_project_for_job(s, org_id, job_id),
                description=reason,
                available=available + cap, held=max(0.0, held - cap),
            )
    except IntegrityError:
        return


def claim_stripe_event(
    s: Session, event_id: str, org_id: str, event_type: str, payload: dict
) -> bool:
    """Record that this webhook was handled. False if it already was.

    The dedupe is the primary key on `stripe_events`, not a SELECT followed by
    an INSERT — two deliveries of the same event arriving at two workers would
    both find nothing and both grant credits. Postgres decides, once.

    Append-only is not the same as idempotent: the ledger will happily accept a
    second `purchase` row, and it is correct to, because a customer really can
    buy two packs. Only the event id knows that these two are the same purchase.
    """
    try:
        with s.begin_nested():
            s.execute(
                sa.insert(m.StripeEvent.__table__).values(
                    id=event_id,
                    org_id=org_id,
                    type=event_type,
                    payload=payload,
                )
            )
    except IntegrityError:
        return False
    return True


def project_spend(s: Session, org_id: str) -> list[dict]:
    """Net credits consumed, per project. What "what has this cost me" means.

    ## Why this is a SUM of deltas rather than a sum of settlements

    The ledger's three job entries are `hold` (−cap), then either `settle`
    (−charged) with `release` of the unused remainder folded into the same
    arithmetic, or `release` (+cap) in full. Summing the deltas is therefore the
    only expression that is right in all three cases at once: a completed job
    nets to what was charged, a cancelled one nets to zero, and a job still
    running shows its hold — which is correct, because that money is genuinely
    unavailable to the customer right now.

    Adding up `settle` rows alone would report a running job as free and a
    cancelled one as free, and the customer's balance would disagree with the
    page telling them where it went.

    ## The bug this had to fix first

    Until now only `hold` carried a `project_id`. `settle` and `release` did
    not, so filtering the ledger by project — which `GET /v1/billing/ledger`
    already offered — returned the holds and nothing else. A finished job
    showed its approved cap rather than what it cost, a cancelled job showed a
    charge that had been refunded in full, and no project's total ever came
    back down. The column existed and was populated on exactly one of the three
    rows that matter.

    `purchase` and `grant` have no project by design: money coming in belongs to
    the organisation, not to whichever project happened to spend it next. They
    are excluded here rather than bucketed under NULL.
    """
    lg = m.CreditLedger.__table__
    rows = s.execute(
        sa.select(
            lg.c.project_id,
            sa.func.sum(-lg.c.delta).label("credits"),
            sa.func.count(sa.distinct(lg.c.job_id)).label("jobs"),
            sa.func.max(lg.c.created_at).label("last_activity"),
        )
        .where(lg.c.org_id == org_id, lg.c.project_id.is_not(None))
        .group_by(lg.c.project_id)
        .order_by(sa.desc("credits"))
    ).all()
    return [
        {
            "project_id": r.project_id,
            "credits": round(float(r.credits or 0), 2),
            "jobs": int(r.jobs or 0),
            "last_activity": r.last_activity,
        }
        for r in rows
    ]


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
