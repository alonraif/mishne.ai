from fastapi import APIRouter, HTTPException

from .. import mock
from ..billing import CREDIT_PACKS, TIERS
from ..schemas import LedgerEntry, Org, PurchaseCreditsRequest

router = APIRouter(prefix="/v1/billing", tags=["billing"])


@router.get("/balance", response_model=Org)
async def get_balance() -> Org:
    return mock.ORG


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
async def get_ledger(project_id: str | None = None) -> list[LedgerEntry]:
    """Per-project usage falls out of filtering the ledger by project_id."""
    if project_id:
        return [e for e in mock.LEDGER if e.project_id == project_id]
    return mock.LEDGER


@router.post("/purchase", status_code=201)
async def purchase_credits(body: PurchaseCreditsRequest) -> dict:
    """Create a Stripe checkout session. Credits are granted on webhook, not here."""
    raise HTTPException(501, "not implemented")


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook() -> dict:
    """Stripe webhook. Must be idempotent — dedupe on Stripe event id."""
    raise HTTPException(501, "not implemented")
