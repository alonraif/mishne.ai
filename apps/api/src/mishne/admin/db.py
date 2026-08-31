"""The admin connection: the one place in this system that bypasses RLS.

`mishne.db.base` connects as `mishne_app`, which has no BYPASSRLS and is
therefore filtered by every policy in the schema. That is what makes the
isolation real, and it is also what makes a back-office impossible from that
connection: "list every organisation" is a question RLS makes unaskable.

So the back-office has its own engine on `admin_database_url`. The engine is
built here rather than borrowed from `db.base` so that there is exactly one
import path to it, and so that grepping for `admin_engine` finds every line of
code in this repository that can see across tenants.

**No `set_org` here, ever.** A scoped session in this process would be a lie:
the role ignores the setting.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from ..config import get_settings
from ..db.base import normalise_url


@lru_cache
def get_engine() -> sa.Engine:
    settings = get_settings()
    return sa.create_engine(
        normalise_url(settings.admin_database_url),
        pool_pre_ping=True,
        # Small on purpose. This is one operator clicking, not a fleet.
        pool_size=2,
        max_overflow=2,
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def transaction() -> Iterator[Session]:
    """One transaction, unscoped and unfiltered."""
    with get_sessionmaker().begin() as session:
        yield session


def db() -> Iterator[Session]:
    """FastAPI dependency form."""
    with transaction() as session:
        yield session


def bypasses_rls() -> bool:
    """Whether the role we actually connected as is exempt from the policies.

    Asserted at startup. Without this the failure is silent and misleading: a
    role without BYPASSRLS sees no rows at all through these queries, so the
    back-office comes up, signs you in, and shows an empty list of
    organisations — which reads as "no customers yet" rather than as
    "misconfigured", and is the sort of thing somebody debugs for an afternoon.

    Superuser implies the exemption, which is why a local compose database
    works with no extra setup.
    """
    with get_engine().connect() as conn:
        return bool(
            conn.execute(
                sa.text(
                    "SELECT rolsuper OR rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
            ).scalar()
        )


def connected_as() -> str:
    with get_engine().connect() as conn:
        return str(conn.execute(sa.text("SELECT current_user")).scalar())
