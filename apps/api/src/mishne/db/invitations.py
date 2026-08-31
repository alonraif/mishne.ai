"""Invitations: creating one, finding one by its token, and using it up.

The token is generated here, hashed here, and returned to the caller exactly
once — the caller emails it and forgets it. Nothing in this module or this
database can recover it, which is the point (migration 0008).

## Reading one without a tenant

Accepting happens before there is a session, so the request is inside no
organisation and every policy fails closed. `find_by_token` opens the narrow
escape the migration created — `app.invitation_token` — which lets a caller
read exactly the row whose secret it presented and nothing else in the table.
It is set `is_local => true`, so it dies with the transaction rather than
surviving onto the next request that borrows the same pooled connection.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from . import models as m


def new_token() -> str:
    """256 bits. Long enough that guessing is not a threat model."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create(
    s: Session,
    org_id: str,
    *,
    email: str,
    role: str,
    invited_by: str | None,
    ttl_days: int,
) -> tuple[str, str]:
    """A new invitation. Returns (id, token) — the token is never stored."""
    token = new_token()
    invitation_id = f"inv_{secrets.token_hex(8)}"
    s.execute(
        sa.insert(m.Invitation.__table__).values(
            id=invitation_id,
            org_id=org_id,
            email=email,
            role=role,
            token_hash=token_hash(token),
            invited_by=invited_by,
            expires_at=datetime.now(timezone.utc) + timedelta(days=ttl_days),
        )
    )
    return invitation_id, token


def find_by_token(s: Session, token: str):
    """The invitation this token names, if it is still usable.

    Expired, revoked and already accepted all return None, and so does a token
    that names nothing. One answer for four cases on purpose: the caller is
    unauthenticated and anything more specific tells a stranger which of their
    guesses was closest.
    """
    hashed = token_hash(token)
    s.execute(
        sa.text("SELECT set_config('app.invitation_token', :t, true)"),
        {"t": hashed},
    )
    table = m.Invitation.__table__
    row = s.execute(
        sa.select(table).where(table.c.token_hash == hashed)
    ).first()
    if row is None:
        return None
    if row.accepted_at is not None or row.revoked_at is not None:
        return None
    if row.expires_at <= datetime.now(timezone.utc):
        return None
    return row


def mark_accepted(s: Session, org_id: str, invitation_id: str, user_id: str) -> None:
    table = m.Invitation.__table__
    s.execute(
        sa.update(table)
        .where(table.c.org_id == org_id, table.c.id == invitation_id)
        .values(accepted_at=sa.func.now(), accepted_user_id=user_id)
    )


def revoke(s: Session, org_id: str, invitation_id: str) -> bool:
    """Withdraw an unaccepted invitation. Returns whether one was withdrawn.

    The row stays. Who was invited, by whom, and that it was thought better of
    is the same access-control record as who joined.
    """
    table = m.Invitation.__table__
    result = s.execute(
        sa.update(table)
        .where(
            table.c.org_id == org_id,
            table.c.id == invitation_id,
            table.c.accepted_at.is_(None),
            table.c.revoked_at.is_(None),
        )
        .values(revoked_at=sa.func.now())
    )
    return bool(result.rowcount)


def pending(s: Session, org_id: str) -> list[dict]:
    """Outstanding invitations, newest first. No tokens, because none exist."""
    table = m.Invitation.__table__
    rows = s.execute(
        sa.select(
            table.c.id, table.c.email, table.c.role, table.c.invited_by,
            table.c.expires_at, table.c.created_at,
        )
        .where(
            table.c.org_id == org_id,
            table.c.accepted_at.is_(None),
            table.c.revoked_at.is_(None),
            table.c.expires_at > sa.func.now(),
        )
        .order_by(table.c.created_at.desc())
    ).all()
    return [
        {"id": r.id, "email": r.email, "role": r.role,
         "invited_by": r.invited_by, "expires_at": r.expires_at,
         "created_at": r.created_at}
        for r in rows
    ]


def outstanding_for(s: Session, org_id: str, email: str):
    """A live invitation for this address, if there is one."""
    table = m.Invitation.__table__
    return s.execute(
        sa.select(table.c.id)
        .where(
            table.c.org_id == org_id,
            sa.func.lower(table.c.email) == email.lower(),
            table.c.accepted_at.is_(None),
            table.c.revoked_at.is_(None),
            table.c.expires_at > sa.func.now(),
        )
    ).first()
