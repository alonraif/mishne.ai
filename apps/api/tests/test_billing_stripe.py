"""Buying credits: on the webhook, once, and never on the redirect.

Workstream C1. The ledger half has existed since B3 — `db/jobs.purchase` writes
an append-only entry and its balance projection in one transaction. What was
missing was the money coming in.

Three properties, and the second is the one that costs real money when it is
wrong:

* **A pack bought with a test card moves the balance, via the webhook.**
* **The same webhook replayed twice grants credits once.** Append-only is not
  idempotent: the ledger will happily take a second `purchase` row, and it is
  right to, because a customer really can buy two packs. Only the Stripe event
  id knows that these two deliveries are one purchase.
* **The redirect grants nothing.** The success URL is the one part of this an
  attacker can visit at will.

The provider is `FakeProvider`, which signs and verifies with a shared secret
and talks to nothing. It is not a canned-response mock — it implements the same
contract, refuses a bad signature included, so the handler under test is the
real handler.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, VIEWER_USER, mint_session, requires_schema  # noqa: E402
from mishne.billing import CREDIT_PACKS  # noqa: E402
from mishne.billing.payments import FakeProvider, PaymentError  # noqa: E402

pytestmark = requires_schema

SECRET = "whsec_test"


def _event(
    event_id: str = "evt_1",
    *,
    org_id: str = ORG,
    pack_id: str = "pack_100",
    type: str = "checkout.session.completed",
    livemode: bool = False,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": type,
            "livemode": livemode,
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "object": "checkout.session",
                    "amount_total": 10_000,
                    "metadata": {"org_id": org_id, "pack_id": pack_id},
                }
            },
        }
    ).encode()


def _post(http, body: bytes, signature: str | None = None):
    provider = FakeProvider(SECRET)
    return http.post(
        "/v1/billing/webhook",
        content=body,
        headers={"stripe-signature": signature or provider.sign(body)},
    )


def _balance(owner) -> float:
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT available FROM org_balances WHERE org_id = :o"),
            {"o": ORG},
        ).first()
    return float(row.available) if row else 0.0


# ── the money arriving ────────────────────────────────────────────────────


@requires_schema
def test_a_purchase_moves_the_balance_through_the_webhook(api, owner):
    http, _ = api
    before = _balance(owner)
    resp = _post(http, _event())

    assert resp.status_code == 200, resp.text
    assert resp.json()["credits"] == CREDIT_PACKS["pack_100"]["credits"]
    # 105 for $100 — the pack's bonus, which is why the ledger is credited from
    # the pack rather than from the amount Stripe collected.
    assert _balance(owner) == before + 105


@requires_schema
def test_the_same_event_twice_grants_credits_once(api, owner):
    """Stripe redelivers. A network blip between their send and our 200 is an
    ordinary Tuesday, and it must not be a second $100 of credits."""
    http, _ = api
    before = _balance(owner)
    first = _post(http, _event("evt_replay"))
    second = _post(http, _event("evt_replay"))

    assert first.json()["status"] == "granted"
    # Still a 200: a non-2xx teaches Stripe to retry an event we handled
    # correctly, which is how one duplicate becomes a thousand.
    assert second.status_code == 200
    assert second.json()["status"] == "already granted"
    assert _balance(owner) == before + 105

    with owner.begin() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT count(*) FROM credit_ledger "
                "WHERE org_id = :o AND stripe_event_id = 'evt_replay'"
            ),
            {"o": ORG},
        ).scalar_one()
    assert rows == 1


@requires_schema
def test_two_different_purchases_both_land(api, owner):
    """The dedupe is on the event, not on the org: a customer buying two packs
    is a customer buying two packs."""
    http, _ = api
    before = _balance(owner)
    _post(http, _event("evt_a"))
    _post(http, _event("evt_b"))
    assert _balance(owner) == before + 210


# ── what is refused ───────────────────────────────────────────────────────


@requires_schema
def test_an_unsigned_body_grants_nothing(api, owner):
    http, _ = api
    before = _balance(owner)
    resp = _post(http, _event("evt_forged"), signature="not-a-signature")

    assert resp.status_code == 400
    assert _balance(owner) == before


@requires_schema
def test_a_forged_org_in_an_unsigned_body_grants_nothing(api, owner):
    """The org comes from metadata we set when we created the session — but
    metadata is only trustworthy because the signature covers it. Changing the
    org means resigning the body, which requires the secret."""
    http, _ = api
    before = _balance(owner)
    resp = _post(http, _event("evt_x", org_id="org_someone_else"),
                 signature="whatever")
    assert resp.status_code == 400
    assert _balance(owner) == before


@requires_schema
def test_an_event_with_no_metadata_is_refused(api, owner):
    """A session we did not create. Nothing to credit, nobody to credit."""
    http, _ = api
    before = _balance(owner)
    resp = _post(http, _event("evt_nometa", pack_id=""))
    assert resp.status_code == 400
    assert _balance(owner) == before


@requires_schema
def test_an_event_we_do_not_care_about_is_acknowledged(api):
    """Stripe sends far more than one event type. 500ing on the rest teaches it
    to retry them forever."""
    http, _ = api
    resp = _post(http, _event("evt_other", type="payment_intent.created"))
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "payment_intent.created"


def test_the_fake_provider_actually_checks_the_signature():
    """If it did not, every test above would pass while proving nothing."""
    provider = FakeProvider(SECRET)
    body = _event()
    assert provider.verify(body, provider.sign(body)).org_id == ORG
    with pytest.raises(PaymentError):
        provider.verify(body, FakeProvider("whsec_other").sign(body))


# ── starting a checkout ───────────────────────────────────────────────────


@requires_schema
def test_starting_a_checkout_grants_nothing(api, owner):
    """The redirect is a courtesy that tells the browser where to go. A
    customer who closes the tab has still paid; a customer who visits the
    success URL without paying has not."""
    http, _ = api
    before = _balance(owner)
    resp = http.post("/v1/billing/purchase", json={"pack_id": "pack_50"})

    assert resp.status_code == 201, resp.text
    assert resp.json()["url"].startswith("https://checkout.test/")
    assert _balance(owner) == before


@requires_schema
def test_only_an_owner_can_buy(api, owner):
    """Billing is the owner's, per the role table in 04-security."""
    http, _ = api
    resp = http.post(
        "/v1/billing/purchase",
        json={"pack_id": "pack_50"},
        headers={"Authorization": f"Bearer {mint_session(owner, ORG, VIEWER_USER)}"},
    )
    assert resp.status_code == 403
