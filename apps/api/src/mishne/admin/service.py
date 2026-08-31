"""The cross-tenant queries and the mutations behind them.

Every function here runs on the admin connection, which is exempt from row-level
security — so every one of them can see and change any organisation's data.
That is the whole point of the module and the reason it is one file: this is the
blast radius, and it should be readable in a sitting.

Two rules hold throughout:

* **Money moves through the ledger**, never by writing `org_balances`. A
  hand-set balance is a number that no longer reconciles to the ledger, and
  reconciliation is an explicit requirement (ADR-0006).
* **Nothing here reads customer content.** Names of organisations and projects,
  yes — the operator has to know which tenant they are looking at. Transcript
  text, brief text, filenames: no. The back-office is for administering
  accounts, not for reading footage, and the moment it can do the second it
  becomes something a customer would object to.
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..db import jobs as job_writes, models as m


class NotFound(Exception):
    """No such organisation."""


# ─────────────────────────────────────────────────────────────────── reading


def list_orgs(s: Session, query: str = "") -> list[dict]:
    """Every tenant, with the numbers an operator triages on.

    One query with correlated subqueries rather than N+1 per org: this is the
    landing screen, and it is the one place where "how many customers are
    there" stops being rhetorical.
    """
    o = m.Org.__table__
    b = m.OrgBalance.__table__
    u = m.User.__table__
    p = m.Project.__table__
    j = m.Job.__table__

    def _count(table, column):
        return (
            sa.select(sa.func.count())
            .select_from(table)
            .where(column == o.c.id)
            .scalar_subquery()
        )

    stmt = (
        sa.select(
            o.c.id, o.c.name, o.c.tier, o.c.retention_days, o.c.created_at,
            o.c.suspended_at, o.c.suspended_reason,
            sa.func.coalesce(b.c.available, 0).label("available"),
            sa.func.coalesce(b.c.held, 0).label("held"),
            _count(u, u.c.org_id).label("user_count"),
            _count(p, p.c.org_id).label("project_count"),
            _count(j, j.c.org_id).label("job_count"),
        )
        .select_from(o.outerjoin(b, b.c.org_id == o.c.id))
        .order_by(o.c.created_at.desc())
    )
    if query.strip():
        like = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            sa.or_(
                sa.func.lower(o.c.name).like(like),
                sa.func.lower(o.c.id).like(like),
            )
        )
    return [_org(row) for row in s.execute(stmt)]


def _org(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "tier": row.tier,
        "retention_days": row.retention_days,
        "created_at": row.created_at,
        "suspended_at": row.suspended_at,
        "suspended_reason": row.suspended_reason,
        "available": float(row.available),
        "held": float(row.held),
        "user_count": row.user_count,
        "project_count": row.project_count,
        "job_count": row.job_count,
    }


def get_org(s: Session, org_id: str) -> dict:
    rows = list_orgs(s)
    for row in rows:
        if row["id"] == org_id:
            return row
    raise NotFound(org_id)


def org_detail(s: Session, org_id: str, *, ledger_limit: int = 50) -> dict:
    """One tenant, deep enough to answer a support email.

    Members, projects with their job counts, the last jobs, and the ledger.
    Deliberately not: transcripts, briefs, or anything a customer would call
    their material.
    """
    org = get_org(s, org_id)
    u = m.User.__table__
    p = m.Project.__table__
    j = m.Job.__table__
    lg = m.CreditLedger.__table__

    members = [
        {
            "id": r.id, "email": r.email, "name": r.name, "role": r.role,
            "created_at": r.created_at,
        }
        for r in s.execute(
            sa.select(u).where(u.c.org_id == org_id).order_by(u.c.created_at)
        )
    ]

    jobs_per_project = (
        sa.select(sa.func.count())
        .select_from(j)
        .where(j.c.project_id == p.c.id)
        .scalar_subquery()
    )
    projects = [
        {
            "id": r.id, "name": r.name, "created_at": r.created_at,
            "job_count": r.job_count, "archived": r.archived_at is not None,
        }
        for r in s.execute(
            sa.select(
                p.c.id, p.c.name, p.c.created_at, p.c.archived_at,
                jobs_per_project.label("job_count"),
            )
            .where(p.c.org_id == org_id)
            .order_by(p.c.created_at.desc())
        )
    ]

    recent_jobs = [
        {
            "id": r.id, "project_id": r.project_id, "status": r.status,
            "mode": r.mode, "created_at": r.created_at,
            "approved_cap": float(r.approved_cap or 0),
            "credits_settled": float(r.credits_settled or 0),
        }
        for r in s.execute(
            sa.select(j)
            .where(j.c.org_id == org_id)
            .order_by(j.c.created_at.desc())
            .limit(25)
        )
    ]

    ledger = [
        {
            "id": r.id, "kind": r.kind, "delta": float(r.delta),
            "balance_after": float(r.balance_after), "description": r.description,
            "project_id": r.project_id, "job_id": r.job_id,
            "created_at": r.created_at,
        }
        for r in s.execute(
            sa.select(lg)
            .where(lg.c.org_id == org_id)
            .order_by(lg.c.created_at.desc())
            .limit(ledger_limit)
        )
    ]

    return {
        **org,
        "members": members,
        "projects": projects,
        "recent_jobs": recent_jobs,
        "ledger": ledger,
    }


def org_audit(s: Session, org_id: str, limit: int = 100) -> list[dict]:
    """The customer's own audit log, read from outside their tenant."""
    a = m.AuditLog.__table__
    return [
        {
            "id": r.id, "action": r.action, "actor_user_id": r.actor_user_id,
            "resource_type": r.resource_type, "resource_id": r.resource_id,
            "at": r.at,
        }
        for r in s.execute(
            sa.select(a)
            .where(a.c.org_id == org_id)
            .order_by(a.c.at.desc())
            .limit(min(limit, 500))
        )
    ]


def actions(s: Session, org_id: str | None = None, limit: int = 100) -> list[dict]:
    """What the back-office has done. Its own log, not a customer's."""
    t = m.PlatformAction.__table__
    admins = m.PlatformAdmin.__table__
    stmt = (
        sa.select(t, admins.c.email.label("admin_email"))
        .select_from(t.outerjoin(admins, admins.c.id == t.c.admin_id))
        .order_by(t.c.created_at.desc())
        .limit(min(limit, 500))
    )
    if org_id:
        stmt = stmt.where(t.c.target_org_id == org_id)
    return [
        {
            "id": r.id, "admin_id": r.admin_id, "admin_email": r.admin_email,
            "action": r.action, "target_org_id": r.target_org_id,
            "target_type": r.target_type, "target_id": r.target_id,
            "reason": r.reason, "detail": r.detail, "created_at": r.created_at,
        }
        for r in s.execute(stmt)
    ]


def totals(s: Session) -> dict:
    """The numbers on the front page. Cheap enough to compute on every load."""
    o = m.Org.__table__
    b = m.OrgBalance.__table__
    j = m.Job.__table__
    return {
        "orgs": s.execute(sa.select(sa.func.count()).select_from(o)).scalar() or 0,
        "suspended": s.execute(
            sa.select(sa.func.count()).select_from(o).where(o.c.suspended_at.isnot(None))
        ).scalar() or 0,
        "credits_outstanding": float(
            s.execute(sa.select(sa.func.coalesce(sa.func.sum(b.c.available), 0))).scalar() or 0
        ),
        "credits_held": float(
            s.execute(sa.select(sa.func.coalesce(sa.func.sum(b.c.held), 0))).scalar() or 0
        ),
        "jobs_running": s.execute(
            sa.select(sa.func.count())
            .select_from(j)
            .where(j.c.status.in_(("queued", "running", "awaiting_media")))
        ).scalar() or 0,
    }


# ─────────────────────────────────────────────────────────────────── writing


def _exists(s: Session, org_id: str) -> None:
    o = m.Org.__table__
    if s.execute(sa.select(o.c.id).where(o.c.id == org_id)).first() is None:
        raise NotFound(org_id)


def grant_credits(s: Session, org_id: str, credits: float) -> float:
    """Put credits on an account. Returns the new available balance.

    Through `db.jobs.grant`, so the ledger and the projection move together and
    the customer can see the line on their own billing screen. Raises
    `InsufficientCredits` if a negative adjustment would take them below zero.
    """
    _exists(s, org_id)
    # The description is what the CUSTOMER sees on their billing screen, so it
    # says what happened in their terms. Why we did it is not here on purpose:
    # the operator's reason goes on the platform action, which is our record,
    # and "non-payment, invoice 41" is not a line to put in front of them.
    label = "credits added by mishne.ai" if credits >= 0 else "balance correction"
    return job_writes.grant(s, org_id, credits, description=label)


def set_tier(s: Session, org_id: str, tier: str) -> None:
    _exists(s, org_id)
    o = m.Org.__table__
    s.execute(sa.update(o).where(o.c.id == org_id).values(tier=tier))


def set_retention(s: Session, org_id: str, days: int) -> None:
    """Retention in days, for customer media.

    The owner-facing endpoint for this has never existed — `audit.py` has had
    the vocabulary entry since 0001 and nothing wrote it. Changing it here does
    not delete anything on its own: the lifecycle rules read this column.
    """
    _exists(s, org_id)
    o = m.Org.__table__
    s.execute(sa.update(o).where(o.c.id == org_id).values(retention_days=days))


def suspend(s: Session, org_id: str, *, reason: str) -> int:
    """Lock a tenant out, and end the sessions they already have.

    Setting the column alone would leave everyone currently signed in working
    normally until their session expired, which for a customer session is up to
    a month. Returns how many sessions were revoked.
    """
    _exists(s, org_id)
    o = m.Org.__table__
    now = datetime.now(timezone.utc)
    s.execute(
        sa.update(o)
        .where(o.c.id == org_id)
        .values(suspended_at=now, suspended_reason=reason)
    )
    sessions = m.Session.__table__
    result = s.execute(
        sa.update(sessions)
        .where(sessions.c.org_id == org_id, sessions.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    return result.rowcount or 0


def unsuspend(s: Session, org_id: str) -> None:
    _exists(s, org_id)
    o = m.Org.__table__
    s.execute(
        sa.update(o)
        .where(o.c.id == org_id)
        .values(suspended_at=None, suspended_reason=None)
    )


#: The order deletion has to happen in, and the reason it is a list rather than
#: one statement. Nothing cascades from `orgs` — `org_id` is a plain column on
#: every table by design — and `job_assets.asset_id` is ON DELETE RESTRICT, so
#: jobs go before assets. `tests/conftest.purge_org` documents the same order
#: for the same reason; this is that operation for real data.
_DELETE_ORDER = (
    "artifacts", "selections", "beat_scores", "beats", "words",
    "speaker_links", "speakers", "transcripts", "job_llm_calls", "job_steps",
    "job_assets", "jobs", "asset_media_requirements", "source_clips", "assets",
    "projects", "invitations", "sessions", "user_credentials", "users",
    "stripe_events", "org_balances",
)


def delete_org(s: Session, org_id: str) -> dict[str, int]:
    """Remove a tenant's rows. Returns what was deleted, per table.

    **`credit_ledger` and `audit_log` are not touched.** Both are append-only at
    the database and a trigger refuses, so this could not delete them without
    dropping that protection — and it should not want to. What "delete this
    customer" means for a financial record and a security log is a real
    question with a real answer (anonymise, or retain under the agreement they
    signed), and it is not a delete button's to assume. The `orgs` row is kept
    for the same reason: those two tables reference it, and an org id in a
    ledger that resolves to nothing is a reconciliation that cannot be done.

    So this frees the storage and ends the access; it does not erase the
    accounting. Media in S3 is the lifecycle rules' job and is not deleted here.
    """
    _exists(s, org_id)
    deleted: dict[str, int] = {}
    for table in _DELETE_ORDER:
        result = s.execute(
            sa.text(f"DELETE FROM {table} WHERE org_id = :o"), {"o": org_id}
        )
        if result.rowcount:
            deleted[table] = result.rowcount
    # Suspended, not removed: the row stays so the ledger still resolves, and
    # nobody can sign in to what is left.
    o = m.Org.__table__
    s.execute(
        sa.update(o)
        .where(o.c.id == org_id)
        .values(
            suspended_at=datetime.now(timezone.utc),
            suspended_reason="deleted by the platform",
        )
    )
    return deleted
