"""A job is priced on every source it draws on.

Workstream C1. A job has taken a list of uploads since B2 and both pricing
paths — the estimate endpoint and job submission — passed `assets[0]`. The two
therefore agreed with each other perfectly, which is why the cap check never
caught it: the displayed price and the charged price were consistently wrong
together. A cut assembled from three two-hour sessions was charged for two
hours, and the error grew with the size of the job.

`packages/shared/src/billing.ts` mirrors `billing/credits.py` and had the same
defect. Both are fixed; these assertions are the reason they have to stay in
step.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.billing import TIERS, estimate_job  # noqa: E402
from mishne.schemas import Asset, Rate  # noqa: E402

TIER = TIERS["pro"]
HOUR_AT_25 = 90_000  # frames


def _asset(frames: int, *, tracks: int = 2, num: int = 25, den: int = 1) -> Asset:
    return Asset(
        id=f"ast_{frames}_{num}",
        project_id="prj_1",
        kind="video",
        ingest_mode="full_media",
        status="ready",
        filename="rushes.mov",
        bytes=1024,
        rate=Rate(num=num, den=den),
        duration_frames=frames,
        drop_frame=False,
        start_tc_frames=0,
        codec="prores",
        audio_tracks=tracks,
        uploaded_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )


def _line(estimate, label: str) -> float:
    return next(line.credits for line in estimate.lines if line.label == label)


def test_three_sources_are_priced_as_three_sources():
    one = estimate_job([_asset(HOUR_AT_25)], TIER, balance=1000)
    three = estimate_job([_asset(HOUR_AT_25)] * 3, TIER, balance=1000)

    assert one.source_hours == 1.0
    assert three.source_hours == 3.0
    # Transcription and the engine scale with the material.
    assert _line(three, "Transcription and alignment") == pytest.approx(
        _line(one, "Transcription and alignment") * 3
    )
    assert _line(three, "Edit engine") == pytest.approx(_line(one, "Edit engine") * 3)


def test_artifacts_are_charged_once_however_many_reels():
    """A job emits one AAF, one FCPXML, one EDL and one transcript however many
    uploads it was cut from. Charging that per asset would be inventing work,
    and it is the obvious thing to get wrong when summing everything else."""
    one = estimate_job([_asset(HOUR_AT_25)], TIER, balance=1000)
    four = estimate_job([_asset(HOUR_AT_25)] * 4, TIER, balance=1000)

    assert _line(one, "Assembly and artifacts") == _line(four, "Assembly and artifacts")


def test_extra_audio_tracks_are_priced_on_their_own_assets_duration():
    """The six-track reel is charged for its extra tracks; the stereo one in the
    same job is not. Pricing the surcharge on the job's total hours would charge
    the multitrack rate for material that has two tracks."""
    multitrack = _asset(HOUR_AT_25, tracks=6)
    stereo = _asset(HOUR_AT_25 * 3, tracks=2)

    both = estimate_job([multitrack, stereo], TIER, balance=1000)
    alone = estimate_job([multitrack], TIER, balance=1000)

    assert _line(both, "Additional audio tracks") == _line(alone, "Additional audio tracks")


def test_a_single_asset_is_still_accepted():
    """"Price this one upload" is a real question the estimate endpoint asks,
    and the mock store asks it too."""
    listed = estimate_job([_asset(HOUR_AT_25)], TIER, balance=1000)
    bare = estimate_job(_asset(HOUR_AT_25), TIER, balance=1000)
    assert bare.model_dump() == listed.model_dump()


def test_mixed_rates_are_conformed_rather_than_added():
    """100 frames at 25 and 100 at 30 are not 200 of anything.

    Duration in seconds is the truth; the frame count is reported at the first
    source's rate, which is what assembly conforms a mixed-rate project to. The
    HANDOVER note is explicit that the AAF writer rejects per-clip rates.
    """
    at_25 = _asset(25 * 60, num=25)          # one minute
    at_30 = _asset(30 * 60, num=30, den=1)   # one minute

    estimate = estimate_job([at_25, at_30], TIER, balance=1000)

    # Two minutes expressed at 25 fps, not 3300 frames of nothing in
    # particular. This is the assertion that matters: it is computed from
    # seconds, so it is exact.
    assert estimate.source_duration_frames == 25 * 120
    # `source_hours` is rounded to two places for display — two minutes is
    # 0.0333 hours and the estimate says 0.03. Asserting the unrounded value
    # here tests the rounding, not the pricing.
    assert estimate.source_hours == 0.03


def test_pricing_no_assets_is_an_error_not_a_free_job():
    with pytest.raises(ValueError):
        estimate_job([], TIER, balance=1000)
