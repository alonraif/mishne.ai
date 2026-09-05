"""The back-office endpoints.

Everything is under `/admin/v1` and everything except sign-in requires an admin
session. There is no read-only role and no scoping: an admin can do all of this
to any tenant, which is what the answer to "I need control over everything"
means and is exactly why the process is separate, the credential is separate,
and every mutation writes a row saying who did it and why.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from ..auth import passwords
from ..config import get_settings
from ..db.jobs import InsufficientCredits
from ..logging import get_logger
from . import actions, auth, service
from .auth import Admin, current_admin
from .db import db
from .schemas import (
    CreateAdminRequest,
    DeleteOrgRequest,
    GrantCreditsRequest,
    LoginRequest,
    SetRetentionRequest,
    SetTierRequest,
    SuspendRequest,
)

router = APIRouter(prefix="/admin/v1")

log = get_logger(__name__)


def _org_or_404(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except service.NotFound as exc:
        raise HTTPException(404, "no such organisation") from exc


# ──────────────────────────────────────────────────────────────── sign in


@router.post("/auth/login")
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    s: Session = Depends(db),
) -> dict:
    ip = actions.client_ip(request)
    admin_id = auth.authenticate(s, body.email, body.password)
    if admin_id is None:
        # In its own transaction: this request is about to raise, and the
        # rollback would take the row with it — see actions.py.
        actions.record_in_its_own_transaction(
            actions.LOGIN_FAILED,
            target_type="admin",
            reason="bad credentials",
            detail={"email_given": bool(body.email)},
            ip=ip,
            user_agent=request.headers.get("user-agent"),
        )
        log.warning("admin.login_failed", ip=ip)
        raise HTTPException(401, "those credentials are not valid")

    token = auth.issue(s, admin_id, ip=ip)
    actions.record(
        s, actions.LOGIN, admin_id=admin_id, target_type="admin",
        target_id=admin_id, reason="sign-in", ip=ip,
        user_agent=request.headers.get("user-agent"),
    )

    settings = get_settings()
    response.set_cookie(
        auth.COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        # Not over plain http on a laptop, always everywhere else. A cookie
        # marked Secure is simply not stored by the browser on http://localhost,
        # which presents as "sign-in appears to work and then you are signed
        # out" rather than as a configuration error.
        secure=settings.environment != "local",
        max_age=settings.admin_session_hours * 3600,
        path="/",
    )
    return {"id": admin_id, "email": body.email.strip().lower()}


@router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> None:
    auth.revoke(s, admin.session_id)
    actions.record(
        s, actions.LOGOUT, admin_id=admin.id, target_type="admin",
        target_id=admin.id, reason="sign-out", ip=actions.client_ip(request),
    )
    # Cleared on the injected response, and nothing is returned. Returning a
    # freshly constructed Response here would throw this header away.
    response.delete_cookie(auth.COOKIE_NAME, path="/")


@router.get("/auth/me")
async def me(admin: Admin = Depends(current_admin)) -> dict:
    return {"id": admin.id, "email": admin.email, "name": admin.name}


@router.post("/admins", status_code=201)
async def create_admin(
    body: CreateAdminRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    """A second operator. Only an existing admin can make one — there is no
    sign-up form, and the first one comes from `bootstrap.py` on the box."""
    try:
        new_id = auth.create_admin(
            s, email=body.email, name=body.name, password=body.password,
            created_by=admin.id,
        )
    except passwords.WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    actions.record(
        s, actions.ADMIN_CREATED, admin_id=admin.id, target_type="admin",
        target_id=new_id, reason="new platform administrator",
        detail={"email": body.email.strip().lower()},
        ip=actions.client_ip(request),
    )
    return {"id": new_id, "email": body.email.strip().lower()}


# ───────────────────────────────────────────────────────────────── reading


@router.get("/overview")
async def overview(
    _: Admin = Depends(current_admin), s: Session = Depends(db)
) -> dict:
    return service.totals(s)


@router.get("/orgs")
async def list_orgs(
    q: str = "", _: Admin = Depends(current_admin), s: Session = Depends(db)
) -> list[dict]:
    return service.list_orgs(s, q)


@router.get("/orgs/{org_id}")
async def get_org(
    org_id: str, _: Admin = Depends(current_admin), s: Session = Depends(db)
) -> dict:
    return _org_or_404(service.org_detail, s, org_id)


@router.get("/orgs/{org_id}/audit")
async def org_audit(
    org_id: str,
    limit: int = 100,
    _: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> list[dict]:
    return service.org_audit(s, org_id, limit)


@router.get("/jobs")
async def list_jobs(
    org_id: str | None = None,
    status: str | None = None,
    failed: bool = False,
    limit: int = 100,
    _: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> list[dict]:
    """Every tenant's jobs, newest first. The support screen.

    `failed=true` is the query an operator runs first and is worth a flag of its
    own rather than making them remember the status name.
    """
    return service.list_jobs(
        s, org_id=org_id, status=status, failed_only=failed, limit=limit
    )


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, _: Admin = Depends(current_admin), s: Session = Depends(db)
) -> dict:
    try:
        return service.job_detail(s, job_id)
    except service.NotFound as exc:
        raise HTTPException(404, "no such job") from exc


@router.get("/actions")
async def platform_actions(
    org_id: str | None = None,
    limit: int = 100,
    _: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> list[dict]:
    return service.actions(s, org_id, limit)


# ───────────────────────────────────────────────────────────────── writing


@router.post("/orgs/{org_id}/credits")
async def grant_credits(
    org_id: str,
    body: GrantCreditsRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    """Put credits on an account, or take them off with a negative amount.

    The manual path, and in the early days the only one: `POST /v1/billing/
    purchase` starts a checkout and grants nothing, and credits otherwise
    arrive only on a signed webhook.
    """
    try:
        after = _org_or_404(service.grant_credits, s, org_id, body.credits)
    except InsufficientCredits as exc:
        raise HTTPException(
            422,
            f"that would take the balance below zero: {exc.available} available",
        ) from exc

    actions.record(
        s,
        actions.CREDITS_GRANTED if body.credits >= 0 else actions.CREDITS_ADJUSTED,
        admin_id=admin.id, org_id=org_id, target_type="org", target_id=org_id,
        reason=body.reason, detail={"credits": body.credits, "balance_after": after},
        ip=actions.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    log.info("admin.credits", org_id=org_id, credits=body.credits, admin_id=admin.id)
    return {"org_id": org_id, "available": after}


@router.patch("/orgs/{org_id}/tier")
async def set_tier(
    org_id: str,
    body: SetTierRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    before = _org_or_404(service.get_org, s, org_id)
    service.set_tier(s, org_id, body.tier)
    actions.record(
        s, actions.TIER_CHANGED, admin_id=admin.id, org_id=org_id,
        target_type="org", target_id=org_id, reason=body.reason,
        detail={"from": before["tier"], "to": body.tier},
        ip=actions.client_ip(request),
    )
    return {"org_id": org_id, "tier": body.tier}


@router.patch("/orgs/{org_id}/retention")
async def set_retention(
    org_id: str,
    body: SetRetentionRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    before = _org_or_404(service.get_org, s, org_id)
    service.set_retention(s, org_id, body.retention_days)
    actions.record(
        s, actions.RETENTION_CHANGED, admin_id=admin.id, org_id=org_id,
        target_type="org", target_id=org_id, reason=body.reason,
        detail={"from": before["retention_days"], "to": body.retention_days},
        ip=actions.client_ip(request),
    )
    return {"org_id": org_id, "retention_days": body.retention_days}


@router.post("/orgs/{org_id}/suspend")
async def suspend(
    org_id: str,
    body: SuspendRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    revoked = _org_or_404(service.suspend, s, org_id, reason=body.reason)
    actions.record(
        s, actions.SUSPENDED, admin_id=admin.id, org_id=org_id,
        target_type="org", target_id=org_id, reason=body.reason,
        detail={"sessions_revoked": revoked}, ip=actions.client_ip(request),
    )
    log.info("admin.suspended", org_id=org_id, sessions_revoked=revoked)
    return {"org_id": org_id, "suspended": True, "sessions_revoked": revoked}


@router.post("/orgs/{org_id}/unsuspend")
async def unsuspend(
    org_id: str,
    body: SuspendRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    _org_or_404(service.unsuspend, s, org_id)
    actions.record(
        s, actions.UNSUSPENDED, admin_id=admin.id, org_id=org_id,
        target_type="org", target_id=org_id, reason=body.reason,
        ip=actions.client_ip(request),
    )
    return {"org_id": org_id, "suspended": False}


@router.post("/orgs/{org_id}/delete")
async def delete_org(
    org_id: str,
    body: DeleteOrgRequest,
    request: Request,
    admin: Admin = Depends(current_admin),
    s: Session = Depends(db),
) -> dict:
    """Delete a tenant's data. POST rather than DELETE, because it has a body.

    The ledger, the audit log and the `orgs` row survive — see
    `service.delete_org` for why that is the right answer rather than a
    limitation.
    """
    org = _org_or_404(service.get_org, s, org_id)
    if body.confirm_name.strip() != org["name"]:
        raise HTTPException(
            422, "the confirmation does not match this organisation's name"
        )
    deleted = service.delete_org(s, org_id)
    actions.record(
        s, actions.DELETED, admin_id=admin.id, org_id=org_id, target_type="org",
        target_id=org_id, reason=body.reason,
        detail={"rows": deleted, "name": org["name"]},
        ip=actions.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    log.warning("admin.org_deleted", org_id=org_id, admin_id=admin.id)
    return {"org_id": org_id, "deleted": deleted}
