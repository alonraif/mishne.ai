"""Signing in to the back-office.

Deliberately not `mishne.auth.sessions` with a flag. A platform admin is not a
row in `users`, holds no membership anywhere, and cannot sign into the product
with this credential — so there is no path by which a compromised customer
login, or a bug in the customer API's session handling, becomes access to every
tenant. The two systems share `auth.passwords` and nothing else.

What is the same, on purpose:

* **The token is never stored** — sha256 of it, exactly as `sessions` and
  `invitations` do.
* **Absent, expired, revoked and disabled are one answer.** Telling them apart
  tells somebody holding a stolen token whether it was ever real.

What is different:

* **Eight hours, not thirty days.** This credential sees every customer's data.
* **No self-service anything.** No sign-up, no password reset, no invitation
  flow. Admins are created by an admin, or by `bootstrap.py` on the box.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import passwords
from . import actions
from ..config import get_settings
from ..db import models as m
from .db import db

#: Its own name. A cookie called `session` on a second origin is the kind of
#: collision that only shows up when both are open in one browser.
COOKIE_NAME = "mishne_admin"


@dataclass(frozen=True)
class Admin:
    """Who a back-office request is. There is no role: an admin can do everything."""

    id: str
    email: str
    name: str
    session_id: str


@lru_cache(maxsize=1)
def _timing_decoy() -> str:
    """A real scrypt hash of a value nobody knows. Computed once per process."""
    return passwords.hash_password(secrets.token_urlsafe(32))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_admin(
    s: Session, *, email: str, name: str, password: str, created_by: str | None = None
) -> str:
    """Raises `passwords.WeakPassword` before anything is written."""
    encoded = passwords.hash_password(password)
    admin_id = f"adm_{secrets.token_hex(8)}"
    s.execute(
        sa.insert(m.PlatformAdmin.__table__).values(
            id=admin_id,
            email=email.strip().lower(),
            name=name.strip(),
            password_hash=encoded,
            created_by=created_by,
        )
    )
    return admin_id


def issue(s: Session, admin_id: str, *, ip: str | None = None) -> str:
    token = secrets.token_urlsafe(32)
    hours = get_settings().admin_session_hours
    s.execute(
        sa.insert(m.PlatformSession.__table__).values(
            id=f"pss_{secrets.token_hex(8)}",
            admin_id=admin_id,
            token_hash=token_hash(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=hours),
            # Coerced, not trusted. This column is `inet`, so an unparseable
            # value does not degrade the record — it raises, and the thing that
            # fails is the sign-in.
            ip=actions.inet(ip),
        )
    )
    return token


def authenticate(s: Session, email: str, password: str) -> str | None:
    """The admin id, or None. One answer for every kind of failure."""
    admins = m.PlatformAdmin.__table__
    row = s.execute(
        sa.select(admins).where(admins.c.email == email.strip().lower())
    ).first()
    if row is None:
        # Spend the time anyway. Returning immediately for an unknown address
        # makes the response time an oracle for which addresses are admins, and
        # there are very few of them to guess at. It has to be a hash with the
        # real cost parameters — verifying against a malformed one returns
        # immediately and equalises nothing.
        passwords.verify(password, _timing_decoy())
        return None
    if row.disabled_at is not None:
        return None
    if not passwords.verify(password, row.password_hash):
        return None
    return row.id


def revoke(s: Session, session_id: str) -> None:
    table = m.PlatformSession.__table__
    s.execute(
        sa.update(table)
        .where(table.c.id == session_id, table.c.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def revoke_all_for(s: Session, admin_id: str) -> int:
    """Every session this admin holds. What disabling one does."""
    table = m.PlatformSession.__table__
    result = s.execute(
        sa.update(table)
        .where(table.c.admin_id == admin_id, table.c.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    return result.rowcount or 0


def resolve(s: Session, token: str) -> Admin | None:
    if not token:
        return None
    sessions = m.PlatformSession.__table__
    admins = m.PlatformAdmin.__table__

    row = s.execute(
        sa.select(sessions).where(sessions.c.token_hash == token_hash(token))
    ).first()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.revoked_at is not None or row.expires_at <= now:
        return None

    admin = s.execute(sa.select(admins).where(admins.c.id == row.admin_id)).first()
    if admin is None or admin.disabled_at is not None:
        return None

    # Last seen, but no sliding expiry. A customer session renews so that
    # somebody working all month is not signed out mid-upload; a back-office
    # session that renews itself never ends, which is the opposite of what an
    # eight-hour lifetime is for.
    s.execute(
        sa.update(sessions).where(sessions.c.id == row.id).values(last_seen_at=now)
    )
    return Admin(id=admin.id, email=admin.email, name=admin.name, session_id=row.id)


def _token(request: Request) -> str:
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def current_admin(
    request: Request, s: Session = Depends(db)
) -> Admin:
    """Who this request is. 401 when the answer is nobody.

    There is no unauthenticated mode and no fixture principal. The customer API
    has one because a developer's machine serves fixtures with nothing to leak;
    this process is pointed at real data by construction.
    """
    admin = resolve(s, _token(request))
    if admin is None:
        raise HTTPException(401, "sign in to the back-office")
    request.state.admin = admin
    return admin
