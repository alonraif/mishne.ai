"""Shared fixtures for the database-backed tests.

Everything here degrades to a skip when there is no Postgres, so the pipeline
tests still run on a machine that has never started docker-compose:

    docker compose -f infra/docker-compose.yml up -d
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

APP_ROLE = "mishne_app"
TEST_LOGIN = "mishne_test_app"
TEST_PASSWORD = "mishne_test_app"

# The database tooling is a declared dependency, but a venv built before B1 —
# or one built from the pipeline list alone — does not have it. Degrade to a
# skip rather than breaking collection for the pipeline tests, which need none
# of this.
try:
    import sqlalchemy as sa

    from mishne.config import get_settings
    from mishne.db.base import get_engine, get_sessionmaker, normalise_url

    _MISSING = ""
except ImportError as exc:  # pragma: no cover - environment, not logic
    sa = None  # type: ignore[assignment]
    _MISSING = f"{exc} — run ./setup.sh"


def _owner_url() -> str:
    return normalise_url(get_settings().database_url)


def _probe(statement: str | None = None) -> bool:
    """Can we connect, and optionally: does `statement` come back true?"""
    if _MISSING:
        return False
    engine = None
    try:
        engine = sa.create_engine(_owner_url(), connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            return True if statement is None else bool(conn.execute(sa.text(statement)).scalar())
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


#: A reachable server. Enough for the migration tests, which build their own
#: scratch database and are the one thing that must run before any schema exists.
requires_postgres = pytest.mark.skipif(
    not _probe(),
    reason=(
        _MISSING
        or "no Postgres — start it with docker compose -f infra/docker-compose.yml up -d"
    ),
)

#: A server with the schema already on it. Everything that queries real tables
#: needs this, and `setup.sh` runs pytest before anyone has had the chance to
#: migrate — so these skip rather than fail on a fresh clone.
requires_schema = pytest.mark.skipif(
    not _probe("SELECT to_regclass('public.orgs') IS NOT NULL"),
    reason=(
        _MISSING
        or "no migrated schema — docker compose up -d, then alembic upgrade head"
    ),
)


@pytest.fixture(scope="session")
def owner():
    """The migration connection. Superuser locally, so it bypasses every policy."""
    engine = sa.create_engine(_owner_url())
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_login(owner) -> str:
    """A login role that is a member of `mishne_app` — and nothing more.

    Created here rather than assumed, so the isolation test is self-contained.
    The two `ALTER`s are the point of the fixture: a superuser or a role with
    BYPASSRLS reads every tenant's rows without raising anything, and a test
    that connects as one passes while proving nothing at all.
    """
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TEST_LOGIN}') THEN
                        CREATE ROLE {TEST_LOGIN} LOGIN PASSWORD '{TEST_PASSWORD}';
                    END IF;
                END
                $$;
                """
            )
        )
        conn.execute(sa.text(f"GRANT {APP_ROLE} TO {TEST_LOGIN}"))
        conn.execute(sa.text(f"ALTER ROLE {TEST_LOGIN} NOSUPERUSER NOBYPASSRLS"))

    url = sa.engine.make_url(_owner_url()).set(
        username=TEST_LOGIN, password=TEST_PASSWORD
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture
def clear_caches():
    """Settings and engines are memoised; a test that changes either must reset both."""
    def _clear() -> None:
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()

    _clear()
    yield _clear
    _clear()
