"""Fixtures mirroring apps/web/src/lib/mock-data.ts.

Served while use_mocks is on, so the web app can develop against real endpoints
returning realistic shapes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .schemas import (
    Artifact,
    Asset,
    EditBrief,
    Job,
    JobStep,
    LedgerEntry,
    Org,
    Project,
    Rate,
    User,
)
from .billing import TIERS, estimate_job
from .pipeline import STEPS

RATE_25 = Rate(num=25, den=1)
RATE_2997 = Rate(num=30000, den=1001)


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


ORG = Org(
    id="org_7fa2",
    name="Northline Post",
    tier="pro",
    credit_balance=142.5,
    credits_held=27,
    retention_days=30,
)

USER = User(
    id="usr_31c8",
    org_id="org_7fa2",
    email="alon@northlinepost.tv",
    name="Alon Raif",
    role="owner",
)

PROJECTS = [
    Project(
        id="prj_harbour",
        org_id="org_7fa2",
        name="Harbour Lights — Ep. 3",
        created_at=_dt("2026-08-14T09:12:00"),
        asset_count=4,
        job_count=6,
        credits_used=168,
    ),
    Project(
        id="prj_summit",
        org_id="org_7fa2",
        name="Nordic Energy Summit",
        created_at=_dt("2026-08-21T14:40:00"),
        asset_count=2,
        job_count=3,
        credits_used=94,
    ),
    Project(
        id="prj_field",
        org_id="org_7fa2",
        name="Field packages — August",
        created_at=_dt("2026-08-03T07:55:00"),
        asset_count=11,
        job_count=11,
        credits_used=231.5,
    ),
]

ASSETS = [
    Asset(
        id="ast_9d41",
        project_id="prj_harbour",
        kind="video",
        ingest_mode="full_media",
        status="ready",
        filename="HARBOUR_EP3_INT_MARGRET_A001.mov",
        bytes=196_142_000_000,
        duration_frames=267_750,
        rate=RATE_25,
        drop_frame=False,
        start_tc_frames=900_000,
        codec="ProRes 422",
        audio_tracks=4,
        uploaded_at=_dt("2026-08-27T08:30:00"),
    ),
    Asset(
        id="ast_2b77",
        project_id="prj_harbour",
        kind="audio",
        ingest_mode="audio_only",
        status="ready",
        filename="HARBOUR_EP3_INT_JONAS_mixdown.wav",
        bytes=362_000_000,
        duration_frames=152_100,
        rate=RATE_25,
        drop_frame=False,
        start_tc_frames=900_000,
        codec="PCM 48k/24",
        audio_tracks=2,
        uploaded_at=_dt("2026-08-27T11:02:00"),
    ),
    Asset(
        id="ast_5e10",
        project_id="prj_summit",
        kind="aaf",
        ingest_mode="aaf_embedded",
        status="ready",
        filename="SUMMIT_KEYNOTE_SELECTS_v4.aaf",
        bytes=84_900_000_000,
        duration_frames=195_804,
        rate=RATE_2997,
        drop_frame=True,
        start_tc_frames=1_079_892,
        codec="DNxHD 145",
        audio_tracks=8,
        uploaded_at=_dt("2026-08-26T13:15:00"),
    ),
]

ESTIMATE = estimate_job(ASSETS[0], TIERS["pro"], ORG.credit_balance)


def _steps(active: int) -> list[JobStep]:
    out = []
    for i, (name, label) in enumerate(STEPS):
        status = "done" if i < active else "active" if i == active else "pending"
        out.append(JobStep(name=name, label=label, status=status))
    return out


JOBS = [
    Job(
        id="job_c41a",
        project_id="prj_harbour",
        asset_id="ast_9d41",
        mode="ai",
        status="analyzing",
        notes_raw=(
            "Ten minutes, tight. Lead on the harbour closure decision — that's the "
            "story. Margret's line about her father's boat has to be in there. Keep "
            "it conversational, not stuffy. Drop anything about the council vote."
        ),
        brief=EditBrief(
            target_duration_s=600,
            duration_tolerance_s=30,
            tone=["conversational", "urgent"],
            narrative_shape="inverted_pyramid",
            must_include=["the harbour closure decision", "Margret's father's boat"],
            must_exclude=["the council vote"],
            speaker_priority=["Margret Olsen"],
            clarifications=[
                'Notes say "tight" — assumed a 30-second tolerance on the 10-minute target.'
            ],
        ),
        steps=_steps(6),
        created_at=_dt("2026-08-28T15:58:00"),
        estimate=ESTIMATE,
    ),
]

ARTIFACTS = [
    Artifact(
        id="art_1",
        job_id="job_8f23",
        kind="aaf",
        filename="HARBOUR_EP3_JONAS_roughcut_v1.aaf",
        bytes=412_000,
        validated=True,
        target_nle="Avid Media Composer",
    ),
    Artifact(
        id="art_2",
        job_id="job_8f23",
        kind="fcpxml",
        filename="HARBOUR_EP3_JONAS_roughcut_v1.fcpxml",
        bytes=186_000,
        validated=True,
        target_nle="Premiere Pro · Resolve · Final Cut",
    ),
]

LEDGER = [
    LedgerEntry(
        id="led_9",
        org_id="org_7fa2",
        project_id="prj_harbour",
        job_id="job_c41a",
        kind="hold",
        delta=-27,
        balance_after=142.5,
        description="Hold for job_c41a",
        created_at=_dt("2026-08-28T15:58:00"),
    ),
    LedgerEntry(
        id="led_6",
        org_id="org_7fa2",
        kind="purchase",
        delta=105,
        balance_after=170.3,
        description="Credit pack — $100 (5 bonus credits)",
        created_at=_dt("2026-08-25T09:30:00"),
    ),
]
