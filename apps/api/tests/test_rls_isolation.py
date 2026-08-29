"""Cross-tenant isolation, proved at the database and not in application code.

This is the test that matters. Customer media here is unreleased IP: a cut of an
unaired episode leaking to another customer is a broadcast incident, not a
privacy incident, and it is not recoverable by apologising.

So the guarantee has to hold below the level anybody can forget. A `WHERE
org_id = ?` that a repository method omits is a leak; a policy the database
enforces is not something a query can omit. Everything below connects as the
role the API actually uses and asks Postgres, not Python.

Read the guards first: `test_the_connecting_role_cannot_bypass_rls` is what
stops the rest of this file from passing vacuously.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Skipped rather than errored in a venv without the database tooling, so the
# pipeline suite still runs. See conftest.
sa = pytest.importorskip("sqlalchemy")

from conftest import requires_schema  # noqa: E402

# Importing the models pulls in SQLAlchemy too, so it goes through the same gate.
ALL_TABLES = pytest.importorskip("mishne.db.models").ALL_TABLES

pytestmark = requires_schema

ORG_A = "org_test_a"
ORG_B = "org_test_b"


@pytest.fixture
def two_orgs(owner: sa.Engine):
    """Two tenants with a project each, inserted as the owner so RLS is not in the way."""
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM projects WHERE org_id IN (:a, :b)"), {"a": ORG_A, "b": ORG_B})
        conn.execute(sa.text("DELETE FROM orgs WHERE id IN (:a, :b)"), {"a": ORG_A, "b": ORG_B})
        conn.execute(
            sa.text(
                "INSERT INTO orgs (id, name, tier, retention_days) VALUES "
                "(:a, 'A Post', 'pro', 30), (:b, 'B Post', 'pro', 30)"
            ),
            {"a": ORG_A, "b": ORG_B},
        )
        conn.execute(
            sa.text(
                "INSERT INTO projects (id, org_id, name) VALUES "
                "('prj_test_a', :a, 'A only'), ('prj_test_b', :b, 'B only')"
            ),
            {"a": ORG_A, "b": ORG_B},
        )
    yield
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM projects WHERE org_id IN (:a, :b)"), {"a": ORG_A, "b": ORG_B})
        conn.execute(sa.text("DELETE FROM orgs WHERE id IN (:a, :b)"), {"a": ORG_A, "b": ORG_B})


def _as_org(url: str, org_id: str | None, statement: str, **params):
    """Run one statement in a transaction scoped the way a request is scoped."""
    engine = sa.create_engine(url)
    try:
        with engine.begin() as conn:
            if org_id is not None:
                conn.execute(
                    sa.text("SELECT set_config('app.org_id', :org, true)"), {"org": org_id}
                )
            result = conn.execute(sa.text(statement), params)
            return result.all() if result.returns_rows else []
    finally:
        engine.dispose()


# ────────────────────────────────────────────────────────────────── the guards


def test_the_connecting_role_cannot_bypass_rls(app_login: str) -> None:
    """Without this, every other test in this file passes and proves nothing.

    A superuser, and any role with BYPASSRLS, reads every tenant's rows with no
    error and no log line. If the API is ever pointed at `database_url` — the
    owner connection migrations use — the policies stay in the schema and stop
    doing anything.
    """
    rows = _as_org(
        app_login,
        None,
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user",
    )
    assert rows, "connecting role not found"
    is_super, bypasses = rows[0]
    assert not is_super, "the application role is a superuser; RLS does not apply to it"
    assert not bypasses, "the application role has BYPASSRLS; RLS does not apply to it"


def test_every_table_has_rls_enabled_forced_and_a_policy(owner: sa.Engine) -> None:
    """A table added without a policy is a table with no tenancy at all.

    FORCE matters as much as ENABLE: without it the owner — which is what
    migrations and any admin script connect as — is exempt from its own policies.
    """
    with owner.connect() as conn:
        rows = conn.execute(
            sa.text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                       (SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind = 'r'
                  AND c.relname = ANY(:tables)
                """
            ),
            {"tables": list(ALL_TABLES)},
        ).all()

    seen = {r[0] for r in rows}
    assert seen == set(ALL_TABLES), f"missing tables: {set(ALL_TABLES) - seen}"
    for name, enabled, forced, policies in rows:
        assert enabled, f"{name}: row level security is not enabled"
        assert forced, f"{name}: RLS is not FORCEd, so the owner bypasses it"
        assert policies >= 1, f"{name}: RLS is on with no policy — the table is unreadable"


def test_every_table_carries_a_not_null_org_id(owner: sa.Engine) -> None:
    """A NULL org_id slips past `org_id = current_setting(...)` — it compares NULL.

    `orgs` is the one exception: its primary key is the tenant key, and its
    policy is written against `id`.
    """
    with owner.connect() as conn:
        rows = conn.execute(
            sa.text(
                """
                SELECT table_name FROM information_schema.columns
                WHERE table_schema = 'public' AND column_name = 'org_id'
                  AND is_nullable = 'NO' AND table_name = ANY(:tables)
                """
            ),
            {"tables": list(ALL_TABLES)},
        ).scalars().all()

    assert set(rows) == set(ALL_TABLES) - {"orgs"}


# ────────────────────────────────────────────────────────────────── the claim


def test_org_a_cannot_read_org_b(app_login: str, two_orgs) -> None:
    rows = _as_org(app_login, ORG_A, "SELECT id, org_id FROM projects ORDER BY id")
    assert [r.id for r in rows] == ["prj_test_a"]
    assert all(r.org_id == ORG_A for r in rows)


def test_naming_the_other_tenant_directly_still_returns_nothing(
    app_login: str, two_orgs
) -> None:
    """The row is not hidden by a filter the query could omit — it is not there.

    A query that asks for B's project by its primary key, while scoped to A,
    gets an empty result rather than a permission error. That is the correct
    shape: an error would confirm the row exists.
    """
    rows = _as_org(
        app_login, ORG_A, "SELECT id FROM projects WHERE id = 'prj_test_b'"
    )
    assert rows == []


def test_a_request_with_no_org_sees_nothing(app_login: str, two_orgs) -> None:
    """Failing closed. An unset session variable must not mean "everything"."""
    rows = _as_org(app_login, None, "SELECT id FROM projects")
    assert rows == []


def test_an_empty_org_is_treated_as_no_org(app_login: str, two_orgs) -> None:
    """`nullif(..., '')` in the policy: a blank org fails closed like an absent one."""
    rows = _as_org(app_login, "", "SELECT id FROM projects")
    assert rows == []


def test_org_a_cannot_write_into_org_b(app_login: str, two_orgs) -> None:
    """WITH CHECK, not just USING. Reading is half of tenancy."""
    with pytest.raises(Exception) as exc:
        _as_org(
            app_login,
            ORG_A,
            "INSERT INTO projects (id, org_id, name) VALUES ('prj_evil', :org, 'planted')",
            org=ORG_B,
        )
    assert "row-level security" in str(exc.value).lower()


def test_org_a_cannot_update_org_bs_rows(app_login: str, two_orgs) -> None:
    """An UPDATE that matches nothing is the correct outcome, not an error."""
    _as_org(
        app_login, ORG_A, "UPDATE projects SET name = 'stolen' WHERE id = 'prj_test_b'"
    )
    rows = _as_org(app_login, ORG_B, "SELECT name FROM projects WHERE id = 'prj_test_b'")
    assert [r.name for r in rows] == ["B only"]


def test_org_a_cannot_delete_org_bs_rows(app_login: str, two_orgs) -> None:
    _as_org(app_login, ORG_A, "DELETE FROM projects WHERE id = 'prj_test_b'")
    rows = _as_org(app_login, ORG_B, "SELECT id FROM projects WHERE id = 'prj_test_b'")
    assert [r.id for r in rows] == ["prj_test_b"]


def test_the_org_scope_does_not_survive_the_transaction(app_login: str, two_orgs) -> None:
    """`set_config(..., is_local => true)` — the scope cannot leak onto the next
    request that borrows the same pooled connection.

    This is the failure that B3's connection pool would otherwise introduce, and
    it would present as one customer intermittently seeing another's projects.
    """
    engine = sa.create_engine(app_login, poolclass=sa.pool.StaticPool)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("SELECT set_config('app.org_id', :org, true)"), {"org": ORG_A}
            )
            assert conn.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 1
        # Same physical connection, new transaction, nothing set.
        with engine.begin() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM projects")).scalar() == 0
    finally:
        engine.dispose()


def test_the_ledger_refuses_to_be_rewritten(owner: sa.Engine) -> None:
    """Append-only at the database (ADR-0006).

    Tested as the owner: if even the owner cannot rewrite a ledger row, nobody
    can, and "balance is a projection of the ledger" stays true.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO orgs (id, name, tier, retention_days) VALUES "
                "(:a, 'A Post', 'pro', 30) ON CONFLICT DO NOTHING"
            ),
            {"a": ORG_A},
        )
        conn.execute(
            sa.text(
                "INSERT INTO credit_ledger (id, org_id, kind, delta, balance_after) "
                "VALUES ('led_test', :a, 'grant', 10, 10)"
            ),
            {"a": ORG_A},
        )
    try:
        with pytest.raises(Exception) as exc:
            with owner.begin() as conn:
                conn.execute(sa.text("UPDATE credit_ledger SET delta = 999 WHERE id = 'led_test'"))
        assert "append-only" in str(exc.value)

        with pytest.raises(Exception) as exc:
            with owner.begin() as conn:
                conn.execute(sa.text("DELETE FROM credit_ledger WHERE id = 'led_test'"))
        assert "append-only" in str(exc.value)
    finally:
        with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text("ALTER TABLE credit_ledger DISABLE TRIGGER credit_ledger_append_only"))
            conn.execute(sa.text("DELETE FROM credit_ledger WHERE id = 'led_test'"))
            conn.execute(sa.text("DELETE FROM orgs WHERE id = :a"), {"a": ORG_A})
            conn.execute(sa.text("ALTER TABLE credit_ledger ENABLE TRIGGER credit_ledger_append_only"))
