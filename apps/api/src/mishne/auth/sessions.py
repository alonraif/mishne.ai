"""Sessions, and the transaction that turns a cookie into a tenant.

This is the piece B4 exists for. Row-level security keys on `app.org_id`, and
the request that has to set it arrives holding nothing but an opaque token. The
sequence, in one transaction:

1. `set_config('app.session_token', sha256(token), true)` — the one narrow
   policy escape on `sessions`, which lets a request read the session row for
   the token it presented and no other row in the table.
2. Read the row. Expired or revoked is the same as absent.
3. `set_config('app.org_id', row.org_id, true)` — from here on every statement
   in the transaction is inside that tenant, and the policies do the isolation.

**`is_local => true` on every one of them.** That ties the value to the
transaction rather than the connection, so it is gone when the transaction ends
and cannot survive onto the next request that borrows the same pooled
connection. A connection carrying one request's org into the next is a
cross-tenant read with no error message anywhere, and it is the single most
dangerous thing in this workstream — `tests/test_pool_isolation.py` exists to
prove it does not happen.

The token itself is 256 bits from `secrets`, and only its SHA-256 is stored. A
dump of the sessions table is therefore a list of session ids rather than a set
of working credentials. No stretching: unlike a password this is already
uniformly random, so there is nothing for a brute force to be faster than.
"""

from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import sqlalchemy as sa
from sqlalchemy.orm import Session as DbSession

from ..db import models as m
from ..db.base import get_sessionmaker, set_org

#: How long a session lasts without being renewed. Long enough that an editor
#: does not sign in again every morning; short enough that a laptop left on a
#: cutting-room floor stops working in a fortnight.
SESSION_TTL = timedelta(days=14)

#: Sessions renew as they are used, but not on every request — a write per
#: request to update a timestamp is a lot of write amplification for a field
#: nobody reads in real time.
RENEW_AFTER = timedelta(hours=6)

COOKIE_NAME = "mishne_session"


@dataclass(frozen=True)
class Principal:
    """Who a request is, once the session has been read. Never trusted from input."""

    user_id: str
    org_id: str
    role: str
    session_id: str
    email: str = ""
    name: str = ""

    def can(self, action: str) -> bool:
        """Roles are deliberately minimal (docs/architecture/04-security.md).

        `viewer` reads and downloads; `member` also uploads and runs jobs;
        `owner` also touches billing and retention. Per-project ACLs are the
        first thing enterprises ask for and the first thing that makes the
        permission model hard — the schema can carry a `project_members` table
        later without a painful migration, and until a customer asks, this is
        the whole model.
        """
        if action == "read":
            return True
        if action == "write":
            return self.role in ("owner", "member")
        if action == "administer":
            return self.role == "owner"
        return False


def new_token() -> str:
    """256 bits. Long enough that guessing is not a threat model."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue(s: DbSession, org_id: str, user_id: str, *, ttl: timedelta = SESSION_TTL) -> str:
    """Create a session row and return the token. The token is never stored."""
    token = new_token()
    s.execute(
        sa.insert(m.Session.__table__).values(
            id=f"ses_{secrets.token_hex(8)}",
            org_id=org_id,
            user_id=user_id,
            token_hash=token_hash(token),
            expires_at=datetime.now(timezone.utc) + ttl,
        )
    )
    return token


def revoke(s: DbSession, org_id: str, session_id: str) -> None:
    table = m.Session.__table__
    s.execute(
        sa.update(table)
        .where(table.c.org_id == org_id, table.c.id == session_id,
               table.c.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )


def revoke_all_for_user(s: DbSession, org_id: str, user_id: str) -> int:
    """Every session this person has. What a password change and a removal do."""
    table = m.Session.__table__
    result = s.execute(
        sa.update(table)
        .where(table.c.org_id == org_id, table.c.user_id == user_id,
               table.c.revoked_at.is_(None))
        .values(revoked_at=datetime.now(timezone.utc))
    )
    return result.rowcount or 0


def set_lookup_token(s: DbSession, hashed: str) -> None:
    """Open the one narrow escape on `sessions`, for this transaction only."""
    s.execute(
        sa.text("SELECT set_config('app.session_token', :t, true)"), {"t": hashed}
    )


def resolve(s: DbSession, token: str) -> Principal | None:
    """A token to a principal, inside a transaction that becomes org-scoped.

    Returns None for absent, expired and revoked alike. Telling them apart would
    tell a caller holding a stolen token whether it was ever real.
    """
    hashed = token_hash(token)
    set_lookup_token(s, hashed)

    sessions = m.Session.__table__
    row = s.execute(
        sa.select(sessions).where(sessions.c.token_hash == hashed)
    ).first()
    if row is None:
        return None
    now = datetime.now(timezone.utc)
    if row.revoked_at is not None or row.expires_at <= now:
        return None

    # From here on the transaction is inside one tenant, and every statement —
    # including the one on the next line — is filtered by the policies.
    set_org(s, row.org_id)

    users = m.User.__table__
    user = s.execute(
        sa.select(users).where(users.c.org_id == row.org_id, users.c.id == row.user_id)
    ).first()
    if user is None:
        # The account was deleted while the session was still valid.
        return None

    if now - row.last_seen_at > RENEW_AFTER:
        s.execute(
            sa.update(sessions)
            .where(sessions.c.org_id == row.org_id, sessions.c.id == row.id)
            .values(last_seen_at=now, expires_at=now + SESSION_TTL)
        )

    return Principal(
        user_id=user.id,
        org_id=row.org_id,
        role=user.role,
        session_id=row.id,
        email=user.email,
        name=user.name,
    )


@contextmanager
def transaction() -> Iterator[DbSession]:
    """A transaction with no tenant set yet. Sees nothing until something sets one."""
    with get_sessionmaker().begin() as session:
        yield session
