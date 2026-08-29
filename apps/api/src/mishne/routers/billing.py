from fastapi import APIRouter, Depends, HTTPException

from ..billing import CREDIT_PACKS, TIERS
from ..schemas import LedgerEntry, Org, PurchaseCreditsRequest
from ..store import Store, get_store

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("/balance", response_model=Org)
async def get_balance(store: Store = Depends(get_store)) -> Org:
    org = store.get_org()
    if org is None:
        raise HTTPException(404, "organisation not found")
    return org


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
    """Per-project usage falls out of filtering the ledger by project_id."""
    return store.list_ledger(project_id)


@router.post("/purchase", status_code=201)
async def purchase_credits(body: PurchaseCreditsRequest) -> dict:
    """Create a Stripe checkout session. Credits are granted on webhook, not here."""
    raise HTTPException(501, "not implemented")


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook() -> dict:
    """Stripe webhook. Must be idempotent — dedupe on Stripe event id."""
    raise HTTPException(501, "not implemented")
