"""FastAPI dependencies: which org a request is for, and a session scoped to it.

Every request runs inside one transaction with `app.org_id` set, so the
row-level security policies decide what it can see. Nothing downstream has to
remember a `WHERE org_id = ...` for the isolation to hold — which is the point
of putting it in the database rather than in a base class somebody can forget to
inherit from.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db.base import session_for_org


def current_org(
    x_org_id: str | None = Header(default=None, alias="X-Org-Id"),
    settings: Settings = Depends(get_settings),
) -> str:
    """Which tenant this request belongs to.

    **This is not authentication.** A header the caller controls is a claim, and
    B4 replaces it with a verified identity. It is deliberately shaped like the
    thing that replaces it — one value, resolved once, handed to the session —
    so that swapping it changes this function and nothing else.

    Outside local development the header is required: falling back to a default
    org would mean an unauthenticated request quietly reading somebody's rows.
    """
    if x_org_id:
        return x_org_id
    if settings.environment == "local":
        return settings.dev_org_id
    raise HTTPException(401, "no organisation on request")


def db(org_id: str = Depends(current_org)) -> Iterator[Session]:
    with session_for_org(org_id) as session:
        yield session


def writable_db(
    org_id: str = Depends(current_org),
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
    with session_for_org(org_id) as session:
        yield session


def serving_mocks(settings: Settings = Depends(get_settings)) -> bool:
    """Whether this process answers from fixtures.

    `Settings` refuses to construct with `use_mocks=True` outside `local`, so
    this can only be true on a developer's machine.
    """
    return settings.use_mocks
