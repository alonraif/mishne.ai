"""FastAPI dependencies: who a request is, and a transaction scoped to their org.

Every request runs inside one transaction with `app.org_id` set, so the
row-level security policies decide what it can see. Nothing downstream has to
remember a `WHERE org_id = ...` for the isolation to hold — which is the point
of putting it in the database rather than in a base class somebody can forget to
inherit from.

**The org comes from the session, never from the request.** Until B4 it came
from an `X-Org-Id` header, which is a claim the caller controls. The header now
works in exactly one place — a local process serving fixtures, where there is no
real data to leak — and nowhere else.

The order matters and is enforced by `session_scope`: the session token opens a
narrow policy escape on `sessions` alone, the row read through it names the org,
and only then is `app.org_id` set. Both settings are transaction-local, so
neither can survive onto the next request that borrows the same pooled
connection.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .auth import sessions
from .auth.sessions import Principal
from .config import Settings, get_settings
from .db.base import session_for_org, set_org

#: The dev principal, for a local process serving fixtures. It is a real
#: `Principal` so that nothing downstream has a second code path, and it exists
#: only where `use_mocks` is permitted — which `Settings` refuses outside
#: `environment=local`.
def _dev_principal(settings: Settings) -> Principal:
    return Principal(
        user_id="usr_dev",
        org_id=settings.dev_org_id,
        role="owner",
        session_id="ses_dev",
        email="dev@localhost",
        name="Local development",
    )


def _token(request: Request) -> str:
    """The session token: a cookie normally, a bearer header for scripts.

    The cookie is `httpOnly`, so the web app cannot read it and therefore cannot
    leak it to injected script. The header exists for the CLI and for tests,
    which have no cookie jar and no XSS to worry about.
    """
    cookie = request.cookies.get(sessions.COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


def current_principal(
    request: Request, settings: Settings = Depends(get_settings)
) -> Principal:
    """Who this request is. 401 when the answer is nobody."""
    token = _token(request)
    if token:
        with sessions.transaction() as s:
            principal = sessions.resolve(s, token)
        if principal is not None:
            request.state.principal = principal
            return principal
        raise HTTPException(401, "your session has expired; sign in again")

    if settings.use_mocks:
        # Fixtures, on a developer's machine. There is nothing to isolate.
        principal = _dev_principal(settings)
        request.state.principal = principal
        return principal

    raise HTTPException(401, "not signed in")


def current_org(principal: Principal = Depends(current_principal)) -> str:
    """The tenant this request belongs to. Derived, never supplied."""
    return principal.org_id


def db(org_id: str = Depends(current_org)) -> Iterator[Session]:
    with session_for_org(org_id) as session:
        yield session


def writable_db(
    principal: Principal = Depends(current_principal),
    settings: Settings = Depends(get_settings),
) -> Iterator[Session]:
    """A session for an endpoint that writes, and a clear refusal without one.

    `use_mocks` serves fixtures from memory, and a fixture cannot be written to.
    Without this guard the first write endpoint hit on a developer's machine
    fails inside psycopg with a connection error, which reads as "the database
    is down" rather than "this process is not talking to one".
    """
    if settings.use_mocks:
        raise HTTPException(
            503,
            "this endpoint writes and the API is serving fixtures; set USE_MOCKS=false",
        )
    with session_for_org(principal.org_id) as session:
        yield session


def require_write(principal: Principal = Depends(current_principal)) -> Principal:
    """`member` or `owner`. A viewer reads and downloads; it does not upload."""
    if not principal.can("write"):
        raise HTTPException(403, "your role does not allow that")
    return principal


def require_owner(principal: Principal = Depends(current_principal)) -> Principal:
    """Billing, retention, and who else is in the organisation."""
    if not principal.can("administer"):
        raise HTTPException(403, "only an owner can do that")
    return principal


def serving_mocks(settings: Settings = Depends(get_settings)) -> bool:
    """Whether this process answers from fixtures.

    `Settings` refuses to construct with `use_mocks=True` outside `local`, so
    this can only be true on a developer's machine.
    """
    return settings.use_mocks


def unscoped_session() -> Iterator[Session]:
    """A transaction with no tenant set. For sign-in, and nothing else.

    Every table's policy fails closed on an unset `app.org_id`, so a query
    issued through this sees nothing at all until something legitimately narrow
    — a session token, an email being signed in with — opens the one row it is
    entitled to. See migration 0003.
    """
    with sessions.transaction() as session:
        yield session


__all__ = [
    "Principal",
    "current_org",
    "current_principal",
    "db",
    "require_owner",
    "require_write",
    "serving_mocks",
    "set_org",
    "unscoped_session",
    "writable_db",
]
