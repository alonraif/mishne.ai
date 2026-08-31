"""The organisation: who is in it, and what they may do.

Roles are deliberately minimal (docs/architecture/04-security.md): an `owner`
does everything including billing and retention, a `member` uploads and runs
jobs, a `viewer` reads and downloads. Per-project ACLs are the first thing
enterprises ask for and the first thing that makes a permission model hard —
the schema can carry a `project_members` table later without a painful
migration, and until a customer asks, this is the whole model.
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit
from ..auth import passwords, sessions
from ..auth.sessions import Principal
from ..config import Settings, get_settings
from ..db import invitations
from ..db import models as m
from ..deps import current_principal, require_owner, writable_db
from ..logging import get_logger
from ..mail import MailError, Message, get_mailer
from ..schemas import Invitation, InviteRequest

router = APIRouter(prefix="/v1/org", tags=["org"])

log = get_logger(__name__)


def _members(s: Session, org_id: str) -> list[dict]:
    users = m.User.__table__
    rows = s.execute(
        sa.select(users).where(users.c.org_id == org_id).order_by(users.c.created_at)
    ).all()
    return [
        {
            "id": r.id,
            "email": r.email,
            "name": r.name,
            "role": r.role,
            "auth_provider": r.auth_provider or "",
        }
        for r in rows
    ]


@router.get("/members")
async def list_members(
    principal: Principal = Depends(current_principal),
    s: Session = Depends(writable_db),
) -> list[dict]:
    """Everyone in this organisation. A viewer may see the list; that is a read."""
    return _members(s, principal.org_id)


@router.post("/members", status_code=201)
async def add_member(
    body: dict,
    request: Request,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> dict:
    """Add someone to this organisation.

    An owner creates the account rather than a stranger joining one: an
    organisation holds unreleased footage, and membership is the whole of the
    access model. A password may be set now, or left empty for an SSO
    organisation where the identity provider is the only thing that
    authenticates anyone.
    """
    email = str(body.get("email", "")).strip().lower()
    role = str(body.get("role", "member"))
    name = str(body.get("name", "")).strip()
    password = str(body.get("password", ""))

    if "@" not in email:
        raise HTTPException(422, "that does not look like an email address")
    if role not in ("owner", "member", "viewer"):
        raise HTTPException(422, "role must be owner, member or viewer")
    if password:
        try:
            passwords.check_strength(password)
        except passwords.WeakPassword as exc:
            raise HTTPException(422, str(exc)) from exc

    user_id = f"usr_{secrets.token_hex(6)}"
    try:
        s.execute(
            sa.insert(m.User.__table__).values(
                id=user_id,
                org_id=principal.org_id,
                email=email,
                name=name,
                role=role,
                auth_provider="local" if password else None,
            )
        )
        if password:
            s.execute(
                sa.insert(m.UserCredential.__table__).values(
                    id=f"crd_{secrets.token_hex(8)}",
                    org_id=principal.org_id,
                    user_id=user_id,
                    password_hash=passwords.hash_password(password),
                )
            )
        s.flush()
    except IntegrityError as exc:
        # An address identifies one person across the whole system (0003), so
        # this is "they already have an account somewhere", which an owner
        # cannot see and should not be told about in detail.
        raise HTTPException(409, "that email address is already in use") from exc

    audit.record(
        s, principal.org_id, audit.MEMBER_ADDED, resource_type="user",
        resource_id=user_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"id": user_id, "email": email, "name": name, "role": role,
            "auth_provider": "local" if password else ""}


@router.post("/members/invite", response_model=Invitation, status_code=201)
async def invite_member(
    body: InviteRequest,
    request: Request,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
    settings: Settings = Depends(get_settings),
) -> Invitation:
    """Offer someone membership, and email them a link to accept it.

    The same decision `POST /members` makes — an owner deciding who may see
    this organisation's footage — with the account created by the person it
    belongs to. Nobody has to choose a stranger's password and then find a
    channel to tell them what it is.

    **The email is sent inside the transaction, and a failure to send undoes
    the invitation.** An invitation row nobody was told about is a link that
    exists and will never be used, sitting in a table an owner reads as "these
    people have been asked". If the mail did not go, the invitation did not
    happen.
    """
    org_id = principal.org_id
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "that does not look like an email address")

    users = m.User.__table__
    if s.execute(
        sa.select(users.c.id).where(
            users.c.org_id == org_id, sa.func.lower(users.c.email) == email
        )
    ).first():
        raise HTTPException(409, "they are already in this organisation")
    if invitations.outstanding_for(s, org_id, email):
        # Not silently replaced: two live links for one address is two ways in,
        # and the owner should know one is already out there.
        raise HTTPException(409, "they already have an invitation waiting")

    invitation_id, token = invitations.create(
        s, org_id,
        email=email,
        role=body.role,
        invited_by=principal.user_id,
        ttl_days=settings.invitation_ttl_days,
    )
    org_name = s.execute(
        sa.select(m.Org.__table__.c.name).where(m.Org.__table__.c.id == org_id)
    ).scalar_one()

    link = f"{settings.app_origin.rstrip('/')}/invite/{token}"
    try:
        get_mailer(settings).send(Message(
            to=email,
            subject=f"{org_name} on mishne.ai",
            body=(
                f"{principal.name or principal.email} has invited you to "
                f"{org_name} on mishne.ai.\n\n"
                f"{link}\n\n"
                f"The link is good for {settings.invitation_ttl_days} days and "
                f"can be used once. If you were not expecting this, ignore it — "
                f"nothing happens until you set a password.\n"
            ),
        ))
    except MailError as exc:
        raise HTTPException(
            502, f"the invitation could not be emailed ({exc}); nothing was sent"
        ) from exc

    audit.record(
        s, org_id, audit.MEMBER_INVITED, resource_type="invitation",
        resource_id=invitation_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    log.info("member.invited", org_id=org_id, role=body.role)
    row = next(i for i in invitations.pending(s, org_id) if i["id"] == invitation_id)
    return Invitation(**{k: row[k] for k in
                         ("id", "email", "role", "expires_at", "created_at")})


@router.get("/invitations", response_model=list[Invitation])
async def list_invitations(
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> list[Invitation]:
    """Who has been asked and has not yet joined.

    Owner only. The member list is readable by anyone in the organisation —
    that is the team — but who has been *offered* a way in is an access-control
    question rather than a roster.
    """
    return [
        Invitation(**{k: row[k] for k in
                      ("id", "email", "role", "expires_at", "created_at")})
        for row in invitations.pending(s, principal.org_id)
    ]


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    invitation_id: str,
    request: Request,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> None:
    """Withdraw an invitation that has not been accepted.

    The row stays and is marked. An invitation thought better of is the same
    kind of record as one accepted, and deleting it loses the fact that
    somebody was once asked.
    """
    if not invitations.revoke(s, principal.org_id, invitation_id):
        raise HTTPException(404, "no invitation waiting with that id")
    audit.record(
        s, principal.org_id, audit.MEMBER_INVITE_REVOKED,
        resource_type="invitation", resource_id=invitation_id,
        actor_user_id=principal.user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )


@router.patch("/members/{user_id}")
async def change_role(
    user_id: str,
    body: dict,
    request: Request,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> dict:
    role = str(body.get("role", ""))
    if role not in ("owner", "member", "viewer"):
        raise HTTPException(422, "role must be owner, member or viewer")

    users = m.User.__table__
    target = s.execute(
        sa.select(users).where(users.c.org_id == principal.org_id, users.c.id == user_id)
    ).first()
    if target is None:
        raise HTTPException(404, "no such member")

    if target.role == "owner" and role != "owner" and _owner_count(s, principal.org_id) < 2:
        # An organisation with no owner cannot change its own billing, its
        # retention policy, or who is in it. There is no support path back.
        raise HTTPException(409, "an organisation needs at least one owner")

    s.execute(
        sa.update(users)
        .where(users.c.org_id == principal.org_id, users.c.id == user_id)
        .values(role=role)
    )
    audit.record(
        s, principal.org_id, audit.MEMBER_ROLE_CHANGED, resource_type="user",
        resource_id=user_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return {"id": user_id, "role": role}


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(
    user_id: str,
    request: Request,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> Response:
    """Remove someone, and end their sessions in the same transaction.

    Removing an account while a signed-in browser keeps working is the gap that
    matters here: the person who was just walked out of the building still has a
    valid cookie until it expires.
    """
    users = m.User.__table__
    target = s.execute(
        sa.select(users).where(users.c.org_id == principal.org_id, users.c.id == user_id)
    ).first()
    if target is None:
        raise HTTPException(404, "no such member")
    if target.role == "owner" and _owner_count(s, principal.org_id) < 2:
        raise HTTPException(409, "an organisation needs at least one owner")

    sessions.revoke_all_for_user(s, principal.org_id, user_id)
    s.execute(
        sa.delete(users).where(users.c.org_id == principal.org_id, users.c.id == user_id)
    )
    audit.record(
        s, principal.org_id, audit.MEMBER_REMOVED, resource_type="user",
        resource_id=user_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    return Response(status_code=204)


@router.get("/audit")
async def read_audit(
    limit: int = 100,
    principal: Principal = Depends(require_owner),
    s: Session = Depends(writable_db),
) -> list[dict]:
    """What happened in this organisation. Owners only, newest first.

    A broadcaster's security review asks for exactly this, and an answer
    assembled from application logs is not an answer.
    """
    log_table = m.AuditLog.__table__
    rows = s.execute(
        sa.select(log_table)
        .where(log_table.c.org_id == principal.org_id)
        .order_by(log_table.c.at.desc())
        .limit(max(1, min(limit, 500)))
    ).all()
    return [
        {
            "at": r.at.isoformat(),
            "action": r.action,
            "actor_user_id": r.actor_user_id,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
        }
        for r in rows
    ]


def _owner_count(s: Session, org_id: str) -> int:
    users = m.User.__table__
    return int(
        s.execute(
            sa.select(sa.func.count())
            .select_from(users)
            .where(users.c.org_id == org_id, users.c.role == "owner")
        ).scalar_one()
    )
