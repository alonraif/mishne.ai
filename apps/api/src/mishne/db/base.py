"""Engine, session, and the org context every query runs inside.

Two connections, deliberately:

* **Migrations** connect as the schema owner (`database_url`). The owner may
  create and drop things.
* **The application** connects as `mishne_app` (`app_database_url`), which holds
  DML and nothing else, is not a superuser, and does not have BYPASSRLS — so
  every statement it issues is filtered by the row-level security policies.

Pointing the API at `database_url` would work, and would silently disable every
policy in the database. See migrations/README.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def normalise_url(url: str) -> str:
    """Force the psycopg3 driver.

    `postgresql://` means psycopg2 to SQLAlchemy 2.0, which is not a dependency
    of this project. The bare form is what belongs in configuration and in
    docker-compose, so the correction happens here rather than in six settings.
    """
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


@lru_cache
def get_engine() -> sa.Engine:
    settings = get_settings()
    return sa.create_engine(
        normalise_url(settings.app_database_url),
        # Pool shape belongs to B3, once there are workers to size it against.
        # pool_pre_ping is the one setting that is right at any size: it costs a
        # round trip and it is what stops a connection killed by a failover from
        # surfacing as a user-visible error.
        pool_pre_ping=True,
        future=True,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


def set_org(session: Session, org_id: str) -> None:
    """Scope every subsequent statement in this transaction to one org.

    `set_config(..., is_local => true)` ties the value to the transaction, so it
    cannot survive onto the next request that borrows the same pooled
    connection. `SET LOCAL` would do the same but takes no bind parameters;
    `set_config` does, which is also what keeps this free of injection.

    Nothing here decides whether the caller is entitled to that org — B4 does.
    """
    session.execute(
        sa.text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id}
    )


@contextmanager
def session_for_org(org_id: str) -> Iterator[Session]:
    """A transaction scoped to one tenant. The only supported way to query."""
    maker = get_sessionmaker()
    with maker.begin() as session:
        set_org(session, org_id)
        yield session
