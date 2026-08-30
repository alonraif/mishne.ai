"""Taking money, behind a provider interface.

The same shape as ASR (ADR-0003) and identity (ADR-0015), for the same reason:
the vendor is a deployment decision, and the test suite must be able to exercise
the whole purchase path without an account, a network, or a key.

## The two rules this file exists to enforce

**Credits are granted on the webhook, never on the redirect.** A customer who
pays and then closes the tab, loses their connection, or gets bounced to a
bank's 3-D Secure page and never comes back has still paid. The redirect is a
courtesy that tells the browser where to go; the webhook is the payment. Any
code path that adds credits because someone arrived at a success URL is a bug,
and the success URL is the one part of this an attacker can visit at will.

**A webhook is not trusted until it is verified.** The endpoint is public and
unauthenticated by construction — Stripe is not going to log in — so the
signature is the whole of the authentication, and the org comes from the
session's metadata rather than from anything the request body merely claims.

## Test mode and live mode

Different keys, different webhook secrets, and the same event shapes. A
test-mode purchase granting real credits is a bug found in production, so
`Event.livemode` is checked against the environment and a mismatch is refused
rather than logged. This costs nothing and removes an entire category of
incident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..logging import get_logger
from .credits import CREDIT_PACKS

log = get_logger(__name__)


class PaymentError(RuntimeError):
    """A payment could not be started or a webhook could not be trusted."""


@dataclass
class CheckoutSession:
    """Where to send the browser. Carries no money and grants no credits."""

    id: str
    url: str


@dataclass
class Event:
    """A verified webhook. Nothing constructs one without checking a signature."""

    id: str
    type: str
    livemode: bool
    #: From the checkout session's metadata, which we set when we created it —
    #: never from a field the caller could choose.
    org_id: str = ""
    pack_id: str = ""
    amount_total: int = 0
    payload: dict = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    name: str

    def create_checkout(
        self, *, org_id: str, pack_id: str, success_url: str, cancel_url: str
    ) -> CheckoutSession: ...

    def verify(self, payload: bytes, signature: str) -> Event: ...


# ── Stripe ────────────────────────────────────────────────────────────────


class StripeProvider:
    """The real one. Imports the SDK lazily so the package is optional."""

    name = "stripe"

    def __init__(self, api_key: str, webhook_secret: str) -> None:
        if not api_key:
            raise PaymentError("stripe_secret_key is not set")
        self._api_key = api_key
        self._webhook_secret = webhook_secret

    def _sdk(self):
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise PaymentError(
                "the stripe package is not installed; pip install '.[payments]'"
            ) from exc
        stripe.api_key = self._api_key
        return stripe

    def create_checkout(
        self, *, org_id: str, pack_id: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        pack = CREDIT_PACKS[pack_id]
        stripe = self._sdk()
        session = stripe.checkout.Session.create(
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": int(pack["amount"] * 100),
                        "product_data": {
                            "name": f"{pack['credits']} mishne.ai credits"
                        },
                    },
                }
            ],
            # The org and the pack travel with the session and come back on the
            # event. This is the only place either is decided — a webhook
            # handler that reads an org from the request body is reading a
            # value an attacker chose.
            metadata={"org_id": org_id, "pack_id": pack_id},
            # Stripe deduplicates a retried CREATE by this key. Our own dedupe
            # is on the event id and is what protects the ledger; this protects
            # the customer from two checkout pages.
            idempotency_key=f"checkout_{org_id}_{pack_id}",
        )
        return CheckoutSession(id=session["id"], url=session["url"])

    def verify(self, payload: bytes, signature: str) -> Event:
        stripe = self._sdk()
        try:
            raw = stripe.Webhook.construct_event(
                payload, signature, self._webhook_secret
            )
        except Exception as exc:  # noqa: BLE001 - every failure is the same answer
            # The type, not the message: a signature error can echo the body.
            raise PaymentError(type(exc).__name__) from exc
        return _event_from(raw)


def _event_from(raw: dict) -> Event:
    """A Stripe event object, narrowed to what the ledger needs."""
    obj = (raw.get("data") or {}).get("object") or {}
    metadata = obj.get("metadata") or {}
    return Event(
        id=raw["id"],
        type=raw["type"],
        livemode=bool(raw.get("livemode", False)),
        org_id=metadata.get("org_id", ""),
        pack_id=metadata.get("pack_id", ""),
        amount_total=int(obj.get("amount_total") or 0),
        payload={"object": obj.get("object", ""), "id": obj.get("id", "")},
    )


# ── the one the tests use ─────────────────────────────────────────────────


class FakeProvider:
    """Signs and verifies with a shared secret. No network, no account.

    This is not a mock in the sense of returning canned answers: it implements
    the same contract, including refusing a bad signature, so a test exercising
    the webhook path exercises the real handler. What it does not do is talk to
    Stripe.
    """

    name = "fake"

    def __init__(self, webhook_secret: str = "whsec_test", livemode: bool = False):
        self._secret = webhook_secret
        self._livemode = livemode

    def create_checkout(
        self, *, org_id: str, pack_id: str, success_url: str, cancel_url: str
    ) -> CheckoutSession:
        if pack_id not in CREDIT_PACKS:
            raise PaymentError(f"no such pack: {pack_id}")
        return CheckoutSession(
            id=f"cs_test_{org_id}_{pack_id}",
            url=f"https://checkout.test/{org_id}/{pack_id}",
        )

    def sign(self, payload: bytes) -> str:
        import hashlib
        import hmac

        return hmac.new(self._secret.encode(), payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> Event:
        import hmac
        import json

        if not hmac.compare_digest(self.sign(payload), signature or ""):
            raise PaymentError("SignatureVerificationError")
        return _event_from(json.loads(payload))


def get_provider(settings=None) -> PaymentProvider:
    from ..config import get_settings

    settings = settings or get_settings()
    if settings.payment_provider == "stripe":
        return StripeProvider(settings.stripe_secret_key, settings.stripe_webhook_secret)
    return FakeProvider(settings.stripe_webhook_secret or "whsec_test")


def credits_for(pack_id: str) -> float:
    pack = CREDIT_PACKS.get(pack_id)
    if pack is None:
        raise PaymentError(f"no such pack: {pack_id}")
    return float(pack["credits"])
