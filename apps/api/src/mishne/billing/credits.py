"""Tiers, credit packs and job estimation.

Mirrors packages/shared/src/billing.ts. The web app computes an estimate for
display; the API recomputes it authoritatively at submission. Never trust a
client-supplied figure — `approved_cap` on a job request is checked against a
freshly computed estimate before a hold is placed.

See docs/architecture/06-billing-and-metering.md.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
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


def _seconds(asset: Asset) -> float:
    return asset.duration_frames * asset.rate.den / asset.rate.num


def estimate_job(
    assets: Asset | Sequence[Asset],
    tier: Tier,
    balance: float,
    mode: JobMode = "ai",
) -> CreditEstimate:
    """Estimate credits for a job, priced on every source it draws on.

    Cost scales with *source* duration, not target cut length: transcription is
    billed per minute of audio and the engine's token count is a function of
    transcript length. Reading three hours is the work; writing ten minutes is not.

    Manual mode skips stages 5-8 entirely — the whole LLM cost — and is not
    charged for them.

    ## Every asset, which it did not used to be

    A job has taken a list of uploads since B2 and this priced the first one.
    The error was in the customer's favour and grew with the size of the job:
    a cut assembled from three two-hour sessions was charged for two hours.
    Both callers — the estimate endpoint and job submission — passed
    `assets[0]`, so the displayed price and the charged price agreed with each
    other and disagreed with the work.

    A single asset is still accepted, because "price this one upload" is a real
    question the estimate endpoint asks.

    ## What is per-job and what is per-asset

    Transcription and the engine scale with total source hours, so they are
    summed. **Artifacts are flat and charged once**: a job emits one AAF, one
    FCPXML, one EDL and one transcript however many uploads it was cut from,
    and charging that four times for a four-reel job would be inventing work.
    Extra audio tracks are per asset, because they are literally per asset — a
    six-track sequence and a stereo one in the same job cost different amounts
    to transcribe.
    """
    if isinstance(assets, Sequence):
        sources = list(assets)
    else:
        sources = [assets]
    if not sources:
        raise ValueError("a job needs at least one asset to be priced")

    source_hours = sum(_seconds(a) for a in sources) / 3600

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

    # Per asset, and priced on that asset's own duration rather than the job's
    # total: the extra tracks belong to the upload that has them.
    extra = [a for a in sources if a.audio_tracks > 2]
    if extra:
        credits = sum(
            _seconds(a) / 3600 * 0.5 * (a.audio_tracks - 2) for a in extra
        )
        detail = (
            f"{extra[0].audio_tracks} tracks transcribed separately"
            if len(extra) == 1
            else f"{len(extra)} sources with more than two tracks"
        )
        lines.append(
            EstimateLine(
                label="Additional audio tracks",
                detail=detail,
                credits=_round2(credits),
            )
        )

    subtotal = _round2(sum(line.credits for line in lines))
    cap = max(MINIMUM_CHARGE, float(math.ceil(subtotal)))

    # Frames at the FIRST source's rate. Summing raw frame counts across
    # sources at different rates adds numbers that do not mean the same thing —
    # 100 frames at 25 and 100 at 30 are not 200 of anything. Conforming to the
    # first asset's rate is what assembly does with a mixed-rate project, so
    # the number a customer sees here matches the timeline they get.
    rate = sources[0].rate
    total_frames = int(round(source_hours * 3600 * rate.num / rate.den))

    return CreditEstimate(
        mode=mode,
        source_duration_frames=total_frames,
        source_hours=_round2(source_hours),
        lines=lines,
        subtotal=subtotal,
        cap=cap,
        balance_before=balance,
        balance_after=_round2(balance - cap),
        sufficient=balance >= cap,
        shortfall=0.0 if balance >= cap else _round2(cap - balance),
    )
