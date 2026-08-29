"""The leak that would have no error message.

`app.org_id` is what every row-level security policy reads. It is set per
request on a connection borrowed from a pool and handed back afterwards. If it
ever survives that handback, the next request on that connection runs as the
previous tenant — and nothing raises, nothing logs, and the symptom is one
customer intermittently seeing another's material. For a platform holding
embargoed footage that is not a privacy incident, it is a broadcast incident.

The mechanism that prevents it is `set_config(..., is_local => true)`, which
ties the value to the transaction rather than the session. These tests exist
because that argument is one word long, easy to undo by accident, and impossible
to notice in review.

They run against a **single** pooled connection on purpose: with a normal pool
the second request usually gets a different connection and the test passes
whether or not the mechanism works.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from conftest import ORG, OWNER_USER, mint_session, requires_schema  # noqa: E402

pytestmark = requires_schema


@pytest.fixture
def one_connection_app(app_login: str, monkeypatch, clear_caches):
    """The API, with a pool of exactly one connection that is never discarded."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")
    monkeypatch.setenv("APP_DATABASE_URL", app_login)
    clear_caches()

    from mishne.db import base

    real_create_engine = sa.create_engine

    def single_connection(url, **kw):
        kw.pop("pool_pre_ping", None)
        return real_create_engine(url, poolclass=sa.pool.StaticPool)

    monkeypatch.setattr(base.sa, "create_engine", single_connection)
    base.get_engine.cache_clear()
    base.get_sessionmaker.cache_clear()

    from mishne.main import app

    with TestClient(app) as client:
        yield client

    base.get_engine.cache_clear()
    base.get_sessionmaker.cache_clear()


def test_one_requests_org_does_not_survive_onto_the_next(
    one_connection_app, tenant, other_tenant, owner
):
    http = one_connection_app
    mine = mint_session(owner, ORG, OWNER_USER)

    # A request that establishes a tenant on the shared connection...
    first = http.get("/v1/auth/me", headers={"Authorization": f"Bearer {mine}"})
    assert first.status_code == 200
    assert first.json()["org"]["id"] == ORG

    # ...and the very next request on the same physical connection, as somebody
    # else. If the setting survived, this would answer with the first org.
    second = http.get("/v1/auth/me", headers={"Authorization": f"Bearer {other_tenant}"})
    assert second.status_code == 200
    assert second.json()["org"]["id"] == "org_test_other"

    # And the projects list, which is the read that would actually leak footage.
    assert http.get(
        "/v1/projects", headers={"Authorization": f"Bearer {other_tenant}"}
    ).json() == []


def test_an_unauthenticated_request_after_a_signed_in_one_sees_nothing(
    one_connection_app, tenant, owner
):
    http = one_connection_app
    mine = mint_session(owner, ORG, OWNER_USER)
    assert http.get("/v1/projects", headers={"Authorization": f"Bearer {mine}"}).status_code == 200

    # Not "sees the previous tenant's rows": refused before a query happens at
    # all, and the connection it would have borrowed is clean anyway.
    assert http.get("/v1/projects").status_code == 401


def test_the_session_lookup_escape_does_not_survive_either(
    one_connection_app, tenant, other_tenant, owner
):
    """`app.session_token` is transaction-local for the same reason.

    It is a narrower grant than the org one — it reads a single session row —
    but a value that outlived its transaction would let the next request on that
    connection read a session that is not its own.
    """
    from mishne.db.base import get_engine

    http = one_connection_app
    mine = mint_session(owner, ORG, OWNER_USER)
    http.get("/v1/auth/me", headers={"Authorization": f"Bearer {mine}"})

    with get_engine().connect() as conn:
        leaked = conn.execute(
            sa.text("SELECT current_setting('app.session_token', true)")
        ).scalar()
        assert leaked in (None, "")
        # And with nothing set, the policies fail closed rather than open.
        assert conn.execute(sa.text("SELECT count(*) FROM sessions")).scalar() == 0
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar() == 0
