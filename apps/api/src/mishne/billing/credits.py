"""Tiers, credit packs and job estimation.

Mirrors packages/shared/src/billing.ts. The web app computes an estimate for
display; the API recomputes it authoritatively at submission. Never trust a
client-supplied figure — `approved_cap` on a job request is checked against a
freshly computed estimate before a hold is placed.

See docs/architecture/06-billing-and-metering.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..schemas import Asset, CreditEstimate, EstimateLine, JobMode

TRANSCRIPTION_RATE_PER_HOUR = 3.5
ARTIFACT_FLAT = 1.0
MINIMUM_CHARGE = 2.0


@dataclass(frozen=True)
class Tier:
    id: str
    name: str
    blurb: str
    monthly_price: float | None
    credit_rate_per_source_hour: float
    max_source_hours: float
    concurrent_jobs: int
    retention_days: int
    sso: bool
    features: list[str] = field(default_factory=list)

    @property
    def engine_rate_per_hour(self) -> float:
        return max(0.0, self.credit_rate_per_source_hour - TRANSCRIPTION_RATE_PER_HOUR)


TIERS: dict[str, Tier] = {
    "starter": Tier(
        id="starter",
        name="Starter",
        blurb="For solo creators finding their workflow.",
        monthly_price=0,
        credit_rate_per_source_hour=12,
        max_source_hours=2,
        concurrent_jobs=1,
        retention_days=7,
        sso=False,
    ),
    "pro": Tier(
        id="pro",
        name="Pro",
        blurb="For working editors and small production teams.",
        monthly_price=49,
        credit_rate_per_source_hour=9,
        max_source_hours=6,
        concurrent_jobs=3,
        retention_days=30,
        sso=False,
    ),
    "studio": Tier(
        id="studio",
        name="Studio",
        blurb="For broadcasters and post houses.",
        monthly_price=None,
        credit_rate_per_source_hour=7,
        max_source_hours=12,
        concurrent_jobs=10,
        retention_days=90,
        sso=True,
    ),
}

CREDIT_PACKS = {
    "pack_50": {"amount": 50, "credits": 50, "bonus": 0},
    "pack_100": {"amount": 100, "credits": 105, "bonus": 5},
    "pack_200": {"amount": 200, "credits": 220, "bonus": 20},
}


def _round2(n: float) -> float:
    return round(n * 100) / 100


def estimate_job(
    asset: Asset, tier: Tier, balance: float, mode: JobMode = "ai"
) -> CreditEstimate:
    """Estimate credits for a job.

    Cost scales with *source* duration, not target cut length: transcription is
    billed per minute of audio and the engine's token count is a function of
    transcript length. Reading three hours is the work; writing ten minutes is not.

    Manual mode skips stages 5-8 entirely — the whole LLM cost — and is not
    charged for them.
    """
    seconds = asset.duration_frames * asset.rate.den / asset.rate.num
    source_hours = seconds / 3600

    lines = [
        EstimateLine(
            label="Transcription and alignment",
            detail=(
                f"{source_hours:.2f} source hours at "
                f"{TRANSCRIPTION_RATE_PER_HOUR} credits/hour"
            ),
            credits=_round2(source_hours * TRANSCRIPTION_RATE_PER_HOUR),
        ),
    ]

    if mode != "manual":
        lines.append(
            EstimateLine(
                label="Edit engine",
                detail=(
                    f"Scoring and selection at {tier.engine_rate_per_hour} "
                    f"credits/hour · {tier.name} rate"
                ),
                credits=_round2(source_hours * tier.engine_rate_per_hour),
            )
        )

    lines.append(
        EstimateLine(
            label="Assembly and artifacts",
            detail="AAF, FCPXML, EDL and transcript",
            credits=ARTIFACT_FLAT,
        )
    )

    if asset.audio_tracks > 2:
        lines.append(
            EstimateLine(
                label="Additional audio tracks",
                detail=f"{asset.audio_tracks} tracks transcribed separately",
                credits=_round2(source_hours * 0.5 * (asset.audio_tracks - 2)),
            )
        )

    subtotal = _round2(sum(line.credits for line in lines))
    cap = max(MINIMUM_CHARGE, float(math.ceil(subtotal)))

    return CreditEstimate(
        mode=mode,
        source_duration_frames=asset.duration_frames,
        source_hours=_round2(source_hours),
        lines=lines,
        subtotal=subtotal,
        cap=cap,
        balance_before=balance,
        balance_after=_round2(balance - cap),
        sufficient=balance >= cap,
        shortfall=0.0 if balance >= cap else _round2(cap - balance),
    )
