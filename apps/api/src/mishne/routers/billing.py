from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import alerts, audit
from ..auth.sessions import Principal
from ..billing import CREDIT_PACKS, TIERS
from ..billing import payments
from ..config import get_settings
from ..db import jobs as job_writes
from ..db.base import session_for_org
from ..deps import current_principal, db as readable_db, require_owner
from ..logging import get_logger
from ..schemas import LedgerEntry, Org, PurchaseCreditsRequest
from ..store import Store, get_store

router = APIRouter(prefix="/v1/billing", tags=["billing"])

log = get_logger(__name__)

#: The event that means the money arrived. Stripe sends a good deal more than
#: this and every other type is acknowledged and ignored — a webhook endpoint
#: that 500s on an event it does not care about teaches Stripe to retry it
#: forever.
PAID = "checkout.session.completed"


@router.get("/balance", response_model=Org)
async def get_balance(store: Store = Depends(get_store)) -> Org:
    org = store.get_org()
    if org is None:
        raise HTTPException(404, "organisation not found")
    return org


@router.get("/balance/warning")
async def balance_warning(
    principal: Principal = Depends(current_principal),
    s: Session = Depends(readable_db),
) -> dict:
    """Whether this org should be told to top up, and why.

    A job refused for want of credits is the worst possible moment to find out:
    the material is uploaded and the person is ready to work. The threshold
    scales with what this org's own jobs cost, because a flat one is trivial for
    a broadcaster and a permanent nag for a hobbyist.
    """
    available, _held = job_writes.balance(s, principal.org_id)
    alert = alerts.low_balance(s, principal.org_id, available=available)
    if alert is None:
        return {"low": False, "available": round(available, 2)}
    # Emitted as well as returned: the customer sees a banner, and we see that
    # a customer is about to be blocked, which is a sales signal and a support
    # signal before it is an error.
    alert.emit()
    return {"low": True, **alert.facts}


@router.get("/tiers")
async def list_tiers() -> dict:
    return {
        k: {
            "id": t.id,
            "name": t.name,
            "blurb": t.blurb,
            "monthly_price": t.monthly_price,
            "credit_rate_per_source_hour": t.credit_rate_per_source_hour,
            "max_source_hours": t.max_source_hours,
            "concurrent_jobs": t.concurrent_jobs,
            "retention_days": t.retention_days,
            "sso": t.sso,
        }
        for k, t in TIERS.items()
    }


@router.get("/packs")
async def list_packs() -> dict:
    return CREDIT_PACKS


@router.get("/ledger", response_model=list[LedgerEntry])
async def get_ledger(
    project_id: str | None = None, store: Store = Depends(get_store)
) -> list[LedgerEntry]:
    """The entries, newest first, optionally for one project.

    This is the transaction list. `GET /billing/projects` is the total, and it
    is a different question: filtering entries gives you the rows, and reading
    a number off them requires knowing that a job is a hold plus a settle plus
    a release and that the three have to be netted.
    """
    return store.list_ledger(project_id)


@router.get("/projects")
async def project_spend(
    principal: Principal = Depends(current_principal),
    s: Session = Depends(readable_db),
) -> list[dict]:
    """What each project has cost, netted across holds, settles and releases.

    Not a sum of settlements: a job still running holds credits the customer
    genuinely cannot spend, and a cancelled job must net to zero rather than
    leaving its cap on the project forever. See `db/jobs.project_spend`.
    """
    return job_writes.project_spend(s, principal.org_id)


@router.post("/purchase", status_code=201)
async def purchase_credits(
    body: PurchaseCreditsRequest,
    request: Request,
    principal: Principal = Depends(require_owner),
) -> dict:
    """Start a checkout. **This grants nothing.**

    It returns a URL to send the browser to. The credits arrive when the webhook
    does, which is the only event that means the money moved — see
    `billing/payments.py`. Owner-only, because buying is a billing action and
    the role table says billing is the owner's.
    """
    settings = get_settings()
    provider = payments.get_provider(settings)
    try:
        session = provider.create_checkout(
            org_id=principal.org_id,
            pack_id=body.pack_id,
            success_url=f"{settings.app_origin}/billing?purchase=complete",
            cancel_url=f"{settings.app_origin}/billing",
        )
    except payments.PaymentError as exc:
        # The type or our own message — never a provider's, which can echo the
        # request body back.
        log.warning("checkout.failed", org_id=principal.org_id,
                    reason=type(exc).__name__)
        raise HTTPException(502, "could not start checkout") from exc

    # In its own transaction: the endpoint has no writable session of its own,
    # and a checkout that was started is a fact whether or not the response
    # makes it back to the browser.
    audit.record_even_if_the_request_fails(
        principal.org_id,
        audit.CHECKOUT_STARTED,
        resource_type="org",
        resource_id=principal.org_id,
        actor_user_id=principal.user_id,
        ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    return {"checkout_id": session.id, "url": session.url}


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(request: Request) -> dict:
    """Where credits are actually granted.

    Unauthenticated by construction — Stripe does not log in — so the signature
    is the whole of the authentication and the org comes from the checkout
    session's metadata, which we set when we created it. Nothing here reads an
    org, an amount or a pack from a field the caller could choose freely.

    Always 200 once the signature verifies, including for events we ignore and
    for a replay: a non-2xx teaches Stripe to redeliver, and redelivering an
    event we have already handled correctly is pure noise.
    """
    settings = get_settings()
    provider = payments.get_provider(settings)
    body = await request.body()

    try:
        event = provider.verify(body, request.headers.get("stripe-signature", ""))
    except payments.PaymentError as exc:
        log.warning("webhook.rejected", reason=str(exc))
        # 400, not 401: this is the one place a rejection should be loud, and
        # Stripe will retry, which is what we want if the secret was rotated
        # badly rather than if we are being probed.
        raise HTTPException(400, "signature verification failed") from exc

    # Test mode and live mode have different keys and different webhook
    # secrets, and identical event shapes. A test-mode purchase granting real
    # credits is a bug you find in production, so it is refused here.
    if settings.environment == "production" and not event.livemode:
        log.error("webhook.mode_mismatch", event_id=event.id, livemode=event.livemode)
        raise HTTPException(400, "test-mode event in a live environment")

    if event.type != PAID:
        return {"ignored": event.type}

    if not event.org_id or not event.pack_id:
        # Metadata we set ourselves is missing, so this session was not created
        # by us. Nothing to credit and nobody to credit it to.
        log.error("webhook.no_metadata", event_id=event.id)
        raise HTTPException(400, "event carries no org")

    credits = payments.credits_for(event.pack_id)
    with session_for_org(event.org_id) as s:
        # The claim and the ledger row are one transaction. If the process dies
        # between them, neither happened, and Stripe's redelivery is the retry.
        first_time = job_writes.claim_stripe_event(
            s, event.id, event.org_id, event.type, event.payload
        )
        if not first_time:
            log.info("webhook.replayed", event_id=event.id, org_id=event.org_id)
            return {"status": "already granted", "event_id": event.id}
        job_writes.purchase(s, event.org_id, credits, stripe_event_id=event.id)

    audit.record_even_if_the_request_fails(
        event.org_id,
        audit.CREDITS_GRANTED,
        resource_type="org",
        resource_id=event.org_id,
        # No actor: nobody was signed in. The money moving is the actor, and
        # the event id is how it is traced back to a person.
        actor_user_id=None,
    )
    log.info("credits.granted", org_id=event.org_id, event_id=event.id,
             credits=credits, pack_id=event.pack_id)
    return {"status": "granted", "credits": credits}
