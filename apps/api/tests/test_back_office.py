"""The platform back-office: what it can reach, and what it must not.

Against a real Postgres, because every claim worth making here is the
database's rather than the application's — that the customer role cannot read a
platform credential, that a grant moves the ledger and the projection together,
and that a suspended tenant's sessions stop working.

Skips itself when the schema is not at 0009 — see conftest.
"""

from __future__ import annotations

import pytest

sa = pytest.importorskip("sqlalchemy")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from conftest import (  # noqa: E402
    ADMIN_EMAIL,
    ADMIN_PASSWORD,
    ORG,
    OWNER_USER,
    mint_session,
    requires_platform_schema,
    requires_schema,
)

pytestmark = [requires_schema, requires_platform_schema]


def _balance(owner, org_id: str = ORG) -> float:
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT available FROM org_balances WHERE org_id = :o"),
            {"o": org_id},
        ).first()
    return float(row.available) if row else 0.0


# ── the boundary the whole design rests on ────────────────────────────────


def test_the_customer_role_cannot_read_the_platform_tables(app_login):
    """`mishne_app` has no privilege on them at all.

    Not "sees no rows" — `permission denied`, from Postgres, before any
    application code runs. This is the assertion that makes the separate
    process worth having: a bug in the customer API cannot read a platform
    credential or forge a platform session, because the role it connects as
    was never granted the table.
    """
    engine = sa.create_engine(app_login)
    try:
        for table in ("platform_admins", "platform_sessions", "platform_actions"):
            with engine.connect() as conn:
                with pytest.raises(Exception) as caught:
                    conn.execute(sa.text(f"SELECT * FROM {table}"))
            assert "permission denied" in str(caught.value).lower(), table
    finally:
        engine.dispose()


def test_the_admin_connection_actually_bypasses_rls(admin_api):
    """If it did not, every list below would be empty and nothing would say so."""
    from mishne.admin.db import bypasses_rls

    assert bypasses_rls()


# ── signing in ────────────────────────────────────────────────────────────


def test_a_wrong_password_is_refused_and_recorded(admin_api, owner):
    http, _ = admin_api
    refused = http.post(
        "/admin/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": "not-the-password"},
    )
    assert refused.status_code == 401

    with owner.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT * FROM platform_actions WHERE action = 'admin.login_failed'")
        ).all()
    # Written in its own transaction, so the 401 does not roll it away.
    assert len(rows) == 1


def test_an_unknown_address_is_the_same_refusal(admin_api):
    http, _ = admin_api
    resp = http.post(
        "/admin/v1/auth/login",
        json={"email": "nobody@example.test", "password": ADMIN_PASSWORD},
    )
    assert resp.status_code == 401
    assert "not valid" in resp.json()["detail"]


def test_nothing_is_reachable_without_a_session(admin_api):
    http, _ = admin_api
    anonymous = {"Cookie": "", "Authorization": ""}
    for method, path in (
        ("get", "/admin/v1/orgs"),
        ("get", "/admin/v1/overview"),
        ("get", "/admin/v1/actions"),
    ):
        resp = getattr(http, method)(path, headers=anonymous)
        assert resp.status_code == 401, path


def test_a_platform_admin_is_not_a_product_login(admin_api, owner):
    """The credential exists in `platform_admins` and in no customer table."""
    with owner.begin() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM users WHERE email = :e"), {"e": ADMIN_EMAIL}
        ).scalar() == 0


# ── seeing across tenants ─────────────────────────────────────────────────


def test_it_sees_every_organisation(admin_api, other_tenant):
    http, _ = admin_api
    orgs = http.get("/admin/v1/orgs").json()
    ids = {o["id"] for o in orgs}
    assert {"org_test_upload", "org_test_other"} <= ids

    mine = next(o for o in orgs if o["id"] == ORG)
    assert mine["user_count"] == 2
    assert mine["available"] == 500.0


def test_org_detail_carries_members_and_ledger_but_no_content(admin_api):
    http, _ = admin_api
    detail = http.get(f"/admin/v1/orgs/{ORG}").json()
    assert {m["id"] for m in detail["members"]} == {"usr_test_owner", "usr_test_viewer"}
    assert "ledger" in detail and "projects" in detail
    # The shape is deliberately account administration, not a window onto the
    # customer's material.
    assert "transcripts" not in detail


def test_an_unknown_org_is_a_404(admin_api):
    http, _ = admin_api
    assert http.get("/admin/v1/orgs/org_nope").status_code == 404


# ── credits, which is the thing this was built for ────────────────────────


def test_a_grant_moves_the_balance_and_the_ledger_together(admin_api, owner):
    http, admin_id = admin_api
    before = _balance(owner)

    resp = http.post(
        f"/admin/v1/orgs/{ORG}/credits",
        json={"credits": 250, "reason": "launch partner, agreed with Alon"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["available"] == before + 250
    assert _balance(owner) == before + 250

    with owner.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT * FROM credit_ledger WHERE org_id = :o AND kind = 'grant' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"o": ORG},
        ).first()
    assert float(row.delta) == 250
    assert float(row.balance_after) == before + 250
    # The ledger still reconstructs the balance, which is the invariant C1 fixed.
    with owner.begin() as conn:
        summed = conn.execute(
            sa.text("SELECT sum(delta) FROM credit_ledger WHERE org_id = :o"),
            {"o": ORG},
        ).scalar()
    assert float(summed) == _balance(owner)


def test_the_grant_is_recorded_with_who_and_why(admin_api, owner):
    http, admin_id = admin_api
    http.post(
        f"/admin/v1/orgs/{ORG}/credits",
        json={"credits": 100, "reason": "goodwill for the failed job on Tuesday"},
    )
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT * FROM platform_actions WHERE action = 'credits.granted'")
        ).first()
    assert row.admin_id == admin_id
    assert row.target_org_id == ORG
    assert "Tuesday" in row.reason
    assert row.detail["credits"] == 100


def test_a_reason_is_not_optional(admin_api):
    http, _ = admin_api
    resp = http.post(f"/admin/v1/orgs/{ORG}/credits", json={"credits": 10})
    assert resp.status_code == 422
    assert http.post(
        f"/admin/v1/orgs/{ORG}/credits", json={"credits": 10, "reason": "x"}
    ).status_code == 422


def test_a_negative_adjustment_is_an_adjustment_row(admin_api, owner):
    http, _ = admin_api
    before = _balance(owner)
    resp = http.post(
        f"/admin/v1/orgs/{ORG}/credits",
        json={"credits": -50, "reason": "double-granted this morning"},
    )
    assert resp.status_code == 200
    assert _balance(owner) == before - 50
    with owner.begin() as conn:
        kind = conn.execute(
            sa.text(
                "SELECT kind FROM credit_ledger WHERE org_id = :o "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"o": ORG},
        ).scalar()
    assert kind == "adjustment"


def test_it_will_not_take_a_balance_below_zero(admin_api, owner):
    http, _ = admin_api
    before = _balance(owner)
    resp = http.post(
        f"/admin/v1/orgs/{ORG}/credits",
        json={"credits": -(before + 1), "reason": "trying to overdraw"},
    )
    assert resp.status_code == 422
    assert _balance(owner) == before


def test_the_customer_sees_the_grant_on_their_own_billing_screen(admin_api, api):
    """A grant is a ledger line, not a number that changed overnight."""
    admin_http, _ = admin_api
    customer_http, _ = api
    admin_http.post(
        f"/admin/v1/orgs/{ORG}/credits",
        json={"credits": 25, "reason": "top-up"},
    )
    ledger = customer_http.get("/v1/billing/ledger").json()
    assert any(e["kind"] == "grant" and e["delta"] == 25 for e in ledger)


# ── tier and retention ────────────────────────────────────────────────────


def test_tier_and_retention_can_be_changed(admin_api, owner):
    http, _ = admin_api
    assert http.patch(
        f"/admin/v1/orgs/{ORG}/tier",
        json={"tier": "studio", "reason": "moved onto the studio plan"},
    ).status_code == 200
    assert http.patch(
        f"/admin/v1/orgs/{ORG}/retention",
        json={"retention_days": 90, "reason": "contractual, 90 days"},
    ).status_code == 200

    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT tier, retention_days FROM orgs WHERE id = :o"), {"o": ORG}
        ).first()
    assert (row.tier, row.retention_days) == ("studio", 90)


def test_an_unknown_tier_is_refused_by_the_schema(admin_api):
    http, _ = admin_api
    resp = http.patch(
        f"/admin/v1/orgs/{ORG}/tier", json={"tier": "enterprise", "reason": "nope"}
    )
    assert resp.status_code == 422


# ── suspension, which has to actually lock somebody out ───────────────────


def test_suspending_revokes_live_sessions_and_blocks_the_api(admin_api, api, owner):
    admin_http, _ = admin_api
    customer_http, _ = api

    assert customer_http.get("/v1/projects").status_code == 200

    suspended = admin_http.post(
        f"/admin/v1/orgs/{ORG}/suspend", json={"reason": "non-payment, invoice 41"}
    )
    assert suspended.status_code == 200
    assert suspended.json()["sessions_revoked"] >= 1

    # The session they were holding no longer resolves at all.
    refused = customer_http.get("/v1/projects")
    assert refused.status_code == 401

    # And a session issued afterwards is refused for the right reason, with a
    # message that does not send them round the sign-in loop forever.
    fresh = mint_session(owner, ORG, OWNER_USER)
    resp = customer_http.get(
        "/v1/projects", headers={"Authorization": f"Bearer {fresh}"}
    )
    assert resp.status_code == 403
    assert "suspended" in resp.json()["detail"]
    assert "invoice 41" in resp.json()["detail"]


def test_unsuspending_lets_them_back_in(admin_api, api, owner):
    admin_http, _ = admin_api
    customer_http, _ = api
    admin_http.post(f"/admin/v1/orgs/{ORG}/suspend", json={"reason": "temporary hold"})
    admin_http.post(f"/admin/v1/orgs/{ORG}/unsuspend", json={"reason": "paid up"})

    token = mint_session(owner, ORG, OWNER_USER)
    resp = customer_http.get(
        "/v1/projects", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200


# ── deletion ──────────────────────────────────────────────────────────────


def test_deleting_needs_the_name_typed_back(admin_api):
    http, _ = admin_api
    resp = http.post(
        f"/admin/v1/orgs/{ORG}/delete",
        json={"confirm_name": "Something Else", "reason": "wrong one"},
    )
    assert resp.status_code == 422


def test_deleting_removes_the_data_and_keeps_the_accounting(admin_api, owner):
    http, _ = admin_api
    http.post(
        f"/admin/v1/orgs/{ORG}/credits", json={"credits": 10, "reason": "before deleting"}
    )
    resp = http.post(
        f"/admin/v1/orgs/{ORG}/delete",
        json={"confirm_name": "Upload test", "reason": "customer asked to close"},
    )
    assert resp.status_code == 200, resp.text

    with owner.begin() as conn:
        counts = {
            table: conn.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE org_id = :o"), {"o": ORG}
            ).scalar()
            for table in ("projects", "users", "sessions", "credit_ledger", "audit_log")
        }
        org = conn.execute(
            sa.text("SELECT * FROM orgs WHERE id = :o"), {"o": ORG}
        ).first()

    assert counts["projects"] == 0
    assert counts["users"] == 0
    assert counts["sessions"] == 0
    # Append-only, and deliberately kept: what they were charged is not a
    # question that stops mattering when they leave.
    assert counts["credit_ledger"] > 0
    assert counts["audit_log"] >= 0
    # The org row survives so the ledger still resolves, and is suspended so
    # nothing is left to sign into.
    assert org is not None and org.suspended_at is not None


# ── the log of what we did ────────────────────────────────────────────────


def test_the_action_log_is_readable_and_append_only(admin_api, owner):
    http, _ = admin_api
    http.post(f"/admin/v1/orgs/{ORG}/credits", json={"credits": 5, "reason": "a fiver"})

    listed = http.get("/admin/v1/actions").json()
    assert any(a["action"] == "credits.granted" for a in listed)
    assert all(a["admin_email"] == ADMIN_EMAIL for a in listed if a["admin_id"])

    with owner.begin() as conn:
        with pytest.raises(Exception) as caught:
            conn.execute(sa.text("UPDATE platform_actions SET reason = 'nope'"))
    assert "append-only" in str(caught.value).lower()


def test_actions_can_be_filtered_to_one_org(admin_api):
    http, _ = admin_api
    http.post(f"/admin/v1/orgs/{ORG}/credits", json={"credits": 5, "reason": "a fiver"})
    filtered = http.get("/admin/v1/actions", params={"org_id": ORG}).json()
    assert filtered and all(a["target_org_id"] == ORG for a in filtered)
    assert http.get("/admin/v1/actions", params={"org_id": "org_nope"}).json() == []
