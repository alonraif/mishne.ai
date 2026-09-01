"""Signing up, signing in, and what a session is allowed to do.

The point of this file is not that a login form works. It is that the org a
request runs as comes from a session the API issued, that a role is enforced,
and that neither of those is the only thing standing between two tenants — the
database is, and `test_pool_isolation.py` is the proof for the case that would
otherwise be silent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from conftest import (  # noqa: E402
    ORG,
    OWNER_USER,
    VIEWER_USER,
    mint_session,
    requires_schema,
)

pytestmark = requires_schema

PASSWORD = "a properly long passphrase"


@pytest.fixture
def app(app_login: str, monkeypatch, clear_caches):
    """The API, against Postgres, with no fixtures anywhere."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")
    monkeypatch.setenv("APP_DATABASE_URL", app_login)
    monkeypatch.setenv("AUTH_PROVIDER", "local")
    # Sign-up is closed by default — access is by invitation. These tests are
    # about what the endpoint does when it is open, and there is a test below
    # for the door being shut.
    monkeypatch.setenv("PUBLIC_SIGNUP", "true")
    clear_caches()
    from mishne.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        yield client


@pytest.fixture
def fresh_org(owner):
    """Remove anything a signup test creates, before and after."""
    emails = ["founder@example.test", "second@example.test", "colleague@example.test",
              # The closed-door test's caller. It should never get an org — but
              # when the door was accidentally open it did, and the org outlived
              # the run with nothing to clean it up.
              "stranger@example.test"]

    def _clean():
        with owner.begin() as conn:
            orgs = conn.execute(
                sa.text("SELECT DISTINCT org_id FROM users WHERE lower(email) = ANY(:e)"),
                {"e": emails},
            ).scalars().all()
            for org_id in orgs:
                conn.execute(
                    sa.text("DELETE FROM projects WHERE org_id = :o"), {"o": org_id}
                )
                conn.execute(sa.text("DELETE FROM users WHERE org_id = :o"), {"o": org_id})
                conn.execute(sa.text("DELETE FROM orgs WHERE id = :o"), {"o": org_id})

    _clean()
    yield emails
    _clean()


# ────────────────────────────────────────────────────────────────────── signup


def test_signing_up_is_closed_unless_it_is_opened(app_login, fresh_org, monkeypatch,
                                                  clear_caches):
    """An organisation holds unreleased footage and membership is the whole of
    the access model, so a public sign-up form is a second door beside the one
    being guarded. It opens deliberately, for a self-serve trial or to create
    the first owner of a deployment."""
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")
    monkeypatch.setenv("APP_DATABASE_URL", app_login)
    # Set, not unset. `Settings` reads `.env` as well as the environment, so
    # deleting the variable does not restore the default on a machine whose
    # `.env` opened the door — and this test asserts what a closed door does,
    # not what the default happens to be where it runs.
    monkeypatch.setenv("PUBLIC_SIGNUP", "false")
    clear_caches()
    from mishne.main import app as fastapi_app

    with TestClient(fastapi_app) as client:
        resp = client.post("/v1/auth/signup", json={
            "email": "stranger@example.test", "password": PASSWORD,
            "org_name": "Uninvited", "name": "Stranger",
        })

    assert resp.status_code == 403
    assert "invitation" in resp.json()["detail"].lower()


def test_signing_up_creates_an_organisation_and_signs_you_in(app, fresh_org, owner):
    response = app.post(
        "/v1/auth/signup",
        json={
            "email": "founder@example.test",
            "password": PASSWORD,
            "org_name": "Harbour Films",
            "name": "Dana",
            "tier": "pro",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["user"]["role"] == "owner"
    assert body["org"]["name"] == "Harbour Films"
    assert body["org"]["tier"] == "pro"
    # The session cookie is httpOnly: the app cannot read it, so a script
    # injected into a page cannot either.
    cookie = response.headers["set-cookie"]
    assert "mishne_session=" in cookie
    assert "HttpOnly" in cookie

    # And the caller is now signed in with it.
    assert app.get("/v1/auth/me").status_code == 200


def test_the_password_is_never_stored(app, fresh_org, owner):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD,
              "org_name": "Harbour Films"},
    )
    with owner.begin() as conn:
        stored = conn.execute(
            sa.text(
                "SELECT c.password_hash FROM user_credentials c "
                "JOIN users u ON u.id = c.user_id WHERE lower(u.email) = :e"
            ),
            {"e": "founder@example.test"},
        ).scalar_one()
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


def test_a_short_password_is_refused_before_an_org_exists(app, fresh_org, owner):
    response = app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": "short", "org_name": "X"},
    )
    assert response.status_code == 422
    with owner.begin() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM users WHERE lower(email) = :e"),
            {"e": "founder@example.test"},
        ).scalar_one() == 0


def test_one_email_is_one_person(app, fresh_org):
    first = {"email": "founder@example.test", "password": PASSWORD, "org_name": "One"}
    assert app.post("/v1/auth/signup", json=first).status_code == 201
    again = app.post("/v1/auth/signup", json={**first, "org_name": "Two"})
    # An address identifies exactly one account, so a login by email is not
    # ambiguous. See migration 0003 and ADR-0015.
    assert again.status_code == 409


# ─────────────────────────────────────────────────────────────────────── login


def test_signing_in_and_out(app, fresh_org):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD,
              "org_name": "Harbour Films"},
    )
    assert app.post("/v1/auth/logout").status_code == 204
    app.cookies.clear()
    assert app.get("/v1/auth/me").status_code == 401

    signed_in = app.post(
        "/v1/auth/login",
        json={"email": "FOUNDER@example.test", "password": PASSWORD},
    )
    assert signed_in.status_code == 200
    assert signed_in.json()["user"]["email"] == "founder@example.test"
    assert app.get("/v1/auth/me").status_code == 200


def test_a_wrong_password_and_an_unknown_account_fail_identically(app, fresh_org):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "X"},
    )
    app.cookies.clear()

    wrong = app.post(
        "/v1/auth/login", json={"email": "founder@example.test", "password": "not it at all"}
    )
    unknown = app.post(
        "/v1/auth/login", json={"email": "nobody@example.test", "password": PASSWORD}
    )

    # Telling them apart turns a login form into a directory of who has an
    # account here, which for a broadcaster is itself sensitive.
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["detail"] == unknown.json()["detail"]


def test_a_revoked_session_stops_working_immediately(app, tenant, owner):
    token = mint_session(owner, ORG, OWNER_USER)
    app.headers.update({"Authorization": f"Bearer {token}"})
    assert app.get("/v1/auth/me").status_code == 200

    app.post("/v1/auth/logout")

    assert app.get("/v1/auth/me").status_code == 401


def test_an_expired_session_is_the_same_as_no_session(app, tenant, owner):
    token = mint_session(owner, ORG, OWNER_USER, expired=True)
    app.headers.update({"Authorization": f"Bearer {token}"})
    assert app.get("/v1/auth/me").status_code == 401


def test_a_forged_token_reads_nothing(app, tenant, owner):
    app.headers.update({"Authorization": "Bearer completely-made-up"})
    assert app.get("/v1/auth/me").status_code == 401


# ──────────────────────────────────────────────────────────────── the org's people


def test_an_owner_can_add_a_colleague_who_can_then_sign_in(app, fresh_org, owner):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "Harbour"},
    )
    added = app.post(
        "/v1/org/members",
        json={"email": "colleague@example.test", "name": "Sam", "role": "member",
              "password": PASSWORD},
    )
    assert added.status_code == 201, added.text

    members = app.get("/v1/org/members").json()
    assert sorted(m["email"] for m in members) == [
        "colleague@example.test", "founder@example.test",
    ]

    app.cookies.clear()
    signed_in = app.post(
        "/v1/auth/login", json={"email": "colleague@example.test", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    # Same organisation, different role.
    assert signed_in.json()["org"]["name"] == "Harbour"
    assert signed_in.json()["user"]["role"] == "member"


def test_a_member_cannot_add_members(app, tenant, owner):
    app.headers.update({"Authorization": f"Bearer {mint_session(owner, ORG, VIEWER_USER)}"})
    response = app.post(
        "/v1/org/members", json={"email": "x@example.test", "role": "member"}
    )
    assert response.status_code == 403


def test_the_last_owner_cannot_be_demoted(app, fresh_org):
    signed_up = app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "Harbour"},
    ).json()
    # An organisation with no owner cannot change its billing, its retention
    # policy, or who is in it, and there is no support path back.
    response = app.patch(
        f"/v1/org/members/{signed_up['user']['id']}", json={"role": "member"}
    )
    assert response.status_code == 409


def test_removing_someone_ends_their_sessions(app, fresh_org, owner):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "Harbour"},
    )
    colleague = app.post(
        "/v1/org/members",
        json={"email": "colleague@example.test", "role": "member", "password": PASSWORD},
    ).json()

    with TestClient(app.app) as theirs:
        assert theirs.post(
            "/v1/auth/login",
            json={"email": "colleague@example.test", "password": PASSWORD},
        ).status_code == 200
        assert theirs.get("/v1/auth/me").status_code == 200

        assert app.delete(f"/v1/org/members/{colleague['id']}").status_code == 204

        # The person who was just walked out of the building must not still have
        # a working cookie.
        assert theirs.get("/v1/auth/me").status_code == 401


# ──────────────────────────────────────────────────────────────────── audit log


def test_the_audit_log_records_who_did_what(app, fresh_org):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "Harbour"},
    )
    app.post("/v1/auth/logout")
    app.post("/v1/auth/login", json={"email": "founder@example.test", "password": PASSWORD})
    app.post("/v1/auth/login", json={"email": "founder@example.test", "password": "wrong one"})

    entries = app.get("/v1/org/audit").json()
    actions = [e["action"] for e in entries]

    assert "org.created" in actions
    assert "user.logout" in actions
    assert "user.login" in actions
    # A failed attempt against a real account is exactly what a security review
    # wants to see.
    assert "user.login_failed" in actions


def test_the_audit_log_cannot_be_rewritten(app, fresh_org, owner):
    app.post(
        "/v1/auth/signup",
        json={"email": "founder@example.test", "password": PASSWORD, "org_name": "Harbour"},
    )
    # Append-only at the database (migration 0001). Tested as the owner: if even
    # the owner cannot rewrite a row, nobody can.
    with pytest.raises(Exception):
        with owner.begin() as conn:
            conn.execute(sa.text("UPDATE audit_log SET action = 'nothing.happened'"))


def test_a_viewer_cannot_read_the_audit_log(app, tenant, owner):
    app.headers.update({"Authorization": f"Bearer {mint_session(owner, ORG, VIEWER_USER)}"})
    assert app.get("/v1/org/audit").status_code == 403
