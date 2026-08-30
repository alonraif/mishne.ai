"""Fixtures mirroring apps/web/src/lib/mock-data.ts and mock-transcript.ts.

Served while `use_mocks` is on, so the web app can develop against real
endpoints returning realistic shapes — and loaded by `db/seed.py` into a real
database, so the same screens render against Postgres. Those two jobs are why
this file has to be complete: anything missing here is a screen that goes blank
the moment `use_mocks` is turned off.

Keep in step with the TypeScript. Where the two fixture sets contradicted each
other, this file is the reconciliation and the divergence is commented:

* **`ast_7c19` is new.** `mock-transcript.ts` describes its second reel as a
  23.976 pickup shoot and calls the mixed-rate case the one "every timecode in
  the UI has to survive" — but it reuses the id of `ast_2b77`, which
  `mock-data.ts` defines as a 25 fps audio mixdown. One asset cannot be both.
  The pickup becomes its own asset, which also moves `prj_harbour` toward the
  asset count it always claimed.
* **`job_8f23` is cut from `ast_9d41`,** not from `ast_2b77`. It is `complete`
  with validated artifacts, and a completed job whose only asset has never been
  transcribed does not describe anything real.
* **Project counts are computed,** not typed in. A hand-written `asset_count`
  is a number that disagrees with the database the day someone adds a fixture.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .billing import TIERS, estimate_job
from .pipeline import STEPS
from .schemas import (
    Artifact,
    Asset,
    Beat,
    EditBrief,
    Job,
    JobStep,
    LedgerEntry,
    Org,
    Project,
    Rate,
    Speaker,
    SpeakerAttribution,
    Transcript,
    TranscriptAsset,
    User,
)

RATE_25 = Rate(num=25, den=1)
RATE_2997 = Rate(num=30000, den=1001)
RATE_2398 = Rate(num=24000, den=1001)


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

# ─────────────────────────────────────────────────────────────────── assets

ASSETS = [
    Asset(
        id="ast_9d41",
        project_id="prj_harbour",
        kind="video",
        ingest_mode="full_media",
        status="ready",
        filename="HARBOUR_EP3_INT_MARGRET_A001.mov",
        bytes=196_142_000_000,
        duration_frames=267_750,  # 2h 58m 30s at 25 fps
        rate=RATE_25,
        drop_frame=False,
        start_tc_frames=900_000,  # 10:00:00:00
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
        duration_frames=152_100,  # 1h 41m 24s
        rate=RATE_25,
        drop_frame=False,
        start_tc_frames=900_000,
        codec="PCM 48k/24",
        audio_tracks=2,
        uploaded_at=_dt("2026-08-27T11:02:00"),
    ),
    # The pickup shoot. Deliberately not at the interview's rate: the studio ran
    # 25 and the location ran 23.976, which is an entirely ordinary thing to
    # find in one project and the case every timecode in the UI has to survive.
    # A fixture where both reels match would let a job-wide rate look correct
    # forever.
    Asset(
        id="ast_7c19",
        project_id="prj_harbour",
        kind="video",
        ingest_mode="full_media",
        status="ready",
        filename="HARBOUR_EP3_PICKUP_B002.mov",
        bytes=18_700_000_000,
        duration_frames=43_200,
        rate=RATE_2398,
        drop_frame=False,
        start_tc_frames=1_251_547,  # 14:30:00:00 at 23.976
        codec="ProRes 422",
        audio_tracks=2,
        uploaded_at=_dt("2026-08-28T09:15:00"),
    ),
    Asset(
        id="ast_5e10",
        project_id="prj_summit",
        kind="aaf",
        ingest_mode="aaf_embedded",
        status="ready",
        filename="SUMMIT_KEYNOTE_SELECTS_v4.aaf",
        bytes=84_900_000_000,
        duration_frames=195_804,  # 1h 48m 52s at 29.97
        rate=RATE_2997,
        drop_frame=True,
        start_tc_frames=1_079_892,
        codec="DNxHD 145",
        audio_tracks=8,
        uploaded_at=_dt("2026-08-26T13:15:00"),
    ),
    # Mid-upload. Every screen that lists assets has to render this state.
    Asset(
        id="ast_ab03",
        project_id="prj_promo",
        kind="video",
        ingest_mode="full_media",
        status="uploading",
        filename="PROMO_Q4_RAW_A002.mp4",
        bytes=31_400_000_000,
        duration_frames=108_000,
        rate=RATE_25,
        drop_frame=False,
        start_tc_frames=0,
        codec="H.264",
        audio_tracks=2,
        uploaded_at=_dt("2026-08-28T15:44:00"),
    ),
]

_ASSETS_BY_ID = {a.id: a for a in ASSETS}

ESTIMATE = estimate_job(ASSETS[0], TIERS["pro"], ORG.credit_balance)


def _steps(active: int, failed_at: int | None = None) -> list[JobStep]:
    out = []
    for i, spec in enumerate(STEPS):
        name, label = spec.name, spec.label
        if failed_at is not None and i == failed_at:
            status = "failed"
        elif i < active:
            status = "done"
        elif i == active:
            status = "active"
        else:
            status = "pending"
        detail = "412 beats · 6 of 9 windows" if status == "active" and name == "score" else None
        out.append(JobStep(name=name, label=label, status=status, detail=detail))
    return out


ALL_DONE = len(STEPS)

# ───────────────────────────────────────────────────────────────────── jobs

JOBS = [
    Job(
        id="job_c41a",
        project_id="prj_harbour",
        asset_ids=["ast_9d41"],
        mode="ai",
        status="analyzing",
        notes_raw=(
            "Ten minutes, tight. Lead on the harbour closure decision — that's the "
            "story. Margret's line about her father's boat has to be in there. Keep "
            "it conversational, not stuffy. Drop anything about the council vote, "
            "we're covering that separately."
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
                'Notes say "tight" — assumed a 30-second tolerance on the 10-minute target.',
                "No preference given on speaker balance beyond Margret; Jonas kept as secondary.",
            ],
        ),
        steps=_steps(6),
        created_at=_dt("2026-08-28T15:58:00"),
        estimate=ESTIMATE,
    ),
    Job(
        id="job_8f23",
        project_id="prj_harbour",
        # ast_9d41, not ast_2b77: this job is complete with validated artifacts,
        # and the mixdown has never been transcribed.
        asset_ids=["ast_9d41"],
        mode="ai",
        status="complete",
        notes_raw=(
            "Six minutes for the web cut. Jonas only. Warm, reflective. Lose the "
            "technical stuff about quota systems."
        ),
        brief=EditBrief(
            target_duration_s=360,
            duration_tolerance_s=20,
            tone=["warm", "reflective"],
            narrative_shape="chronological",
            must_exclude=["quota systems"],
            speaker_priority=["Jonas Berg"],
            pacing="breathing",
        ),
        steps=_steps(ALL_DONE),
        created_at=_dt("2026-08-27T11:40:00"),
        finished_at=_dt("2026-08-27T12:14:00"),
        estimate=ESTIMATE.model_copy(update={"cap": 16.0, "subtotal": 15.4}),
        credits_settled=14.8,
    ),
    Job(
        id="job_1d90",
        project_id="prj_summit",
        asset_ids=["ast_5e10"],
        mode="ai",
        status="failed",
        notes_raw="Twelve minutes. Keynote highlights, energy transition focus.",
        brief=EditBrief(
            target_duration_s=720,
            duration_tolerance_s=45,
            tone=["authoritative"],
            narrative_shape="thematic",
            handle_frames=8,
        ),
        # Failed at the validation gate — the last step, after everything else
        # succeeded. That is the expensive failure and the one worth rendering.
        steps=_steps(ALL_DONE, failed_at=len(STEPS) - 1),
        created_at=_dt("2026-08-26T14:02:00"),
        finished_at=_dt("2026-08-26T14:51:00"),
        estimate=ESTIMATE.model_copy(update={"cap": 14.0, "subtotal": 13.2}),
        error=(
            "Round-trip validation failed: AAF clip count 47 does not match "
            "timeline (48). Credits were refunded."
        ),
    ),
    Job(
        id="job_2e57",
        project_id="prj_harbour",
        # Two uploads in one cut, at two different rates. Any screen that renders
        # a job must survive this, not just the single-asset case.
        asset_ids=["ast_9d41", "ast_7c19"],
        mode="hybrid",
        status="awaiting_edit",
        notes_raw="Give me a starting point for the web version and I'll take it from there.",
        brief=EditBrief(
            # Scaled to the transcript fixture, which is a 26-beat excerpt rather
            # than a full three-hour beat list. Keeps the target gauge meaningful.
            target_duration_s=120,
            duration_tolerance_s=20,
            tone=["conversational"],
            narrative_shape="chronological",
            pacing="breathing",
        ),
        steps=_steps(9),
        created_at=_dt("2026-08-28T14:10:00"),
        estimate=ESTIMATE.model_copy(update={"cap": 28.0, "subtotal": 27.4}),
    ),
]

# Newest first, as the job list renders and the query returns.
JOBS.sort(key=lambda j: j.created_at, reverse=True)

JOB_BY_ID = {j.id: j for j in JOBS}

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
    Artifact(
        id="art_3",
        job_id="job_8f23",
        kind="edl",
        filename="HARBOUR_EP3_JONAS_roughcut_v1.edl",
        bytes=9_400,
        validated=True,
        target_nle="Universal fallback",
    ),
    Artifact(
        id="art_4",
        job_id="job_8f23",
        kind="otio",
        filename="HARBOUR_EP3_JONAS_roughcut_v1.otio",
        bytes=244_000,
        validated=True,
        target_nle="Canonical timeline",
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
        description="Hold for job_c41a · Harbour Lights — Ep. 3",
        created_at=_dt("2026-08-28T15:58:00"),
    ),
    LedgerEntry(
        id="led_8",
        org_id="org_7fa2",
        project_id="prj_summit",
        job_id="job_1d90",
        kind="refund",
        delta=14,
        balance_after=169.5,
        description="Refund — job_1d90 failed validation",
        created_at=_dt("2026-08-26T14:51:00"),
    ),
    LedgerEntry(
        id="led_7",
        org_id="org_7fa2",
        project_id="prj_harbour",
        job_id="job_8f23",
        kind="settle",
        delta=-14.8,
        balance_after=155.5,
        description="Settled job_8f23 · 14.80 of 16.00 approved",
        created_at=_dt("2026-08-27T12:14:00"),
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
    LedgerEntry(
        id="led_5",
        org_id="org_7fa2",
        project_id="prj_field",
        kind="settle",
        delta=-21.4,
        balance_after=65.3,
        description="Settled job_7c02 · Field packages — August",
        created_at=_dt("2026-08-24T17:05:00"),
    ),
]
# Most recent first — a ledger is read newest-first, and the API orders it that
# way.
LEDGER.sort(key=lambda e: e.created_at, reverse=True)

# ─────────────────────────────────────────────────────── transcript and beats
#
# A representative excerpt. A real three-hour interview produces several hundred
# beats and the page virtualizes them; these 26 exercise every state — used,
# unused, each flag, each speaker, high and low scores — which is what the design
# needs.
#
# The last four beats are the pickup shoot. Their frame numbers are local to that
# reel, which is the whole point: 11:20:50 on B is not 11:20:50 on A. Following
# mock-transcript.ts, the seed timecodes are laid out on a 25 fps grid for both
# reels — the fixture is exercising *which* reel a beat belongs to, not the
# arithmetic of two rates, and timecode.py already has an exhaustive self-test
# for that.

def _tc(h: int, m: int, s: int) -> int:
    return (h * 3600 + m * 60 + s) * 25


#: (start_frames, speaker, text, used, score, flags, rationale)
_SEEDS: list[tuple[int, str, str, bool, float, list[str], str | None]] = [
    (_tc(10, 2, 14), "T3", "Just tell me when you're comfortable and we'll start whenever you like.", False, 4, [], None),
    (_tc(10, 2, 33), "T1", "Um, yeah. Yeah, I'm — I'm fine. Go ahead.", False, 3, ["filler", "false_start"], None),
    (_tc(10, 3, 2), "T1", "My father kept his boat in the east basin for forty-one years. The Sigrún. She's still there, tied up, and she hasn't been out since March.", False, 88, [], "Strong delivery, but superseded — the subject gave the same line again at 10:03:43 with the corrected figure. Same redundancy cluster; only one member can be selected."),
    (_tc(10, 3, 26), "T1", "Sorry, can I say that again? I want to get the years right.", False, 2, ["false_start"], None),
    (_tc(10, 3, 43), "T1", "My father kept his boat in the east basin for forty-three years. The Sigrún. She hasn't left the harbour since March, and she won't again.", True, 96, ["retake"], "Later take of the same line, higher confidence and the corrected figure. Preferred over the earlier delivery."),
    (_tc(10, 5, 12), "T1", "People keep saying the harbour is closing. It isn't closing. It's being closed. There's a difference and everybody here knows exactly what it is.", True, 98, [], "The strongest line in the interview. Quotable, sharp, and it frames the closure as a decision rather than an event — which is the angle the notes asked for."),
    (_tc(10, 6, 30), "T1", "The dredging costs came in at, I think it was, four point two million? Something like that. And that was the number that did it.", False, 41, ["low_confidence"], None),
    (_tc(10, 9, 5), "T2", "I've been harbour master for nineteen years. I have signed off on every vessel that's come through that gate. And in February I signed the notice that says they can't.", True, 92, [], "Jonas's credential and the turn in one beat. Works as the second voice without needing separate setup."),
    (_tc(10, 11, 18), "T2", "The quota system changed in twenty-three, and then again in twenty-five, and the tonnage thresholds moved with it, so what you had was a fleet that was compliant on Monday and non-compliant on Tuesday without anybody doing anything differently.", False, 58, [], None),
    (_tc(10, 14, 2), "T2", "No, I don't blame the council. I blame the arithmetic. The council just read it out.", True, 89, [], "Gives the piece a second register — resigned rather than angry. Balances Margret's edge."),
    (_tc(10, 18, 44), "T1", "There were sixty-two boats working out of here when I took over from my father. Sixty-two. There are nine.", True, 95, [], "The scale of the decline in a single comparison. Numbers the viewer can hold."),
    (_tc(10, 21, 10), "T3", "And what happens to the nine?", True, 71, [], "Short interviewer question retained because the answer that follows depends on it."),
    (_tc(10, 22, 0), "T1", "Two are going to Þórshöfn. Three are being sold south. The rest of us are, well. We're waiting to see what the compensation looks like, and nobody will tell us.", True, 91, [], "Direct answer to the retained question. Concrete outcomes, and the unresolved ending gives the section somewhere to go."),
    (_tc(10, 26, 15), "T1", "I mean the compensation, sorry, the — the transition package, that's what they call it. The transition package.", False, 34, ["false_start", "filler"], None),
    (_tc(10, 31, 40), "T2", "You can measure a harbour by the ice plant. If the ice plant runs, the harbour is alive. Ours stopped in April and nobody has asked me to start it again.", True, 93, [], "Vivid, specific detail that does the emotional work without stating it. Strong candidate for the closing section."),
    (_tc(10, 35, 12), "T2", "The council vote was on the fourteenth and it went through eleven to two.", False, 12, [], None),
    (_tc(10, 38, 50), "T1", "My daughter asked me last week whether she should learn the boat. And I didn't have an answer for her. That's the first time that's happened.", True, 97, [], "Emotional peak. Personal, forward-looking, and it lands the consequence on the next generation."),
    (_tc(10, 44, 20), "T1", "Would I do it again? Yes. Obviously yes. That's not — that was never the question.", True, 88, [], "Natural closing beat. Resolves without resolving, which suits the piece."),
    (_tc(10, 52, 30), "T2", "There's a lot of paperwork involved in closing something. More than opening it, I'd say. Much more.", False, 52, [], None),
    (_tc(11, 4, 10), "T1", "The east basin freezes first. Always has. My father used to say you could set your calendar by it.", False, 64, [], None),
    (_tc(11, 12, 44), "T2", "I'll be the last one out. Somebody has to lock it.", True, 90, [], "Final line of the cut. Short, definitive, and it closes the harbour master's arc."),
    (_tc(11, 20, 0), "T3", "Is there anything you want to add that I haven't asked about?", False, 8, [], None),
    # ── the pickup shoot, reel B, its own reel time ──────────────────────────
    (_tc(11, 20, 50), "T1", "No. No, I think that's — I think that's it, really.", False, 11, ["filler"], None),
    (_tc(11, 34, 12), "T1", "[overlapping] — well no, but that's exactly what I — sorry, go on.", False, 6, ["crosstalk", "false_start"], None),
    (_tc(11, 48, 30), "T2", "The gate itself is from nineteen sixty-eight. It still works. Everything here still works, that's the thing.", False, 69, [], None),
    (_tc(12, 2, 15), "T1", "[off mic] You want me to say that bit about the ice again?", False, 5, ["off_mic"], None),
]

#: Conversational interview delivery sits around 2.6 words per second. Deriving
#: beat length from the text keeps durations plausible instead of arbitrary.
_WORDS_PER_SECOND = 2.6
_MIN_BEAT_SECONDS = 1.6

INTERVIEW_ASSET = "ast_9d41"
PICKUP_ASSET = "ast_7c19"
_PICKUP_FROM = len(_SEEDS) - 4


def _duration_frames(text: str) -> int:
    words = len(text.split())
    return round(max(_MIN_BEAT_SECONDS, words / _WORDS_PER_SECOND) * 25)


def _canonical_speaker(local: str, asset_id: str) -> str:
    """Resolve a per-asset speaker id to the merged one the UI groups by.

    Speakers are local to an asset — "T1" on reel B is a different row from "T1"
    on reel A, and stays a different person until someone merges them. Margret
    was merged; the pickup's second mic was not, so it keeps a qualified id and
    shows up in the legend on its own.
    """
    if asset_id == PICKUP_ASSET and local != "T1":
        return f"{local}@{asset_id}"
    return local


BEATS: list[Beat] = []
for _i, (_start, _spk, _text, _used, _score, _flags, _why) in enumerate(_SEEDS):
    _asset = PICKUP_ASSET if _i >= _PICKUP_FROM else INTERVIEW_ASSET
    BEATS.append(
        Beat(
            id=f"beat_{_i + 1:03d}",
            idx=_i,
            asset_id=_asset,
            speaker=_canonical_speaker(_spk, _asset),
            start_frames=_start,
            end_frames=_start + _duration_frames(_text),
            text=_text,
            flags=_flags,
            used=_used,
            score=_score,
            rationale=_why,
        )
    )

# Cut order, in source order, over the used beats.
_order = 0
for _b in BEATS:
    if _b.used:
        _b.order_idx = _order
        _order += 1

#: One row per (asset, voice) — the shape of the `speakers` table.
#:
#: (asset_id, local id, default label, label, confirmed, track, words, speech_ms)
#:
#: Two named and confirmed on the interview, one still carrying the microphone
#: it came down, and a pickup reel with its own two. A mock where every speaker
#: already has a name would imply the system works names out on its own, which
#: it does not and cannot.
SPEAKER_ROWS: list[tuple[str, str, str, str, bool, int, int, int]] = [
    (INTERVIEW_ASSET, "T1", "Mic 1", "Margret Olsen", True, 1, 388, 203_000),
    (INTERVIEW_ASSET, "T2", "Mic 2", "Jonas Berg", True, 2, 244, 140_000),
    (INTERVIEW_ASSET, "T3", "Mic 3", "", False, 3, 47, 22_000),
    (PICKUP_ASSET, "T1", "Mic 1", "Margret Olsen", True, 1, 24, 11_000),
    (PICKUP_ASSET, "T2", "Mic 2", "", False, 2, 24, 11_000),
]

#: The merges a person actually made — the `speaker_links` rows.
#: (project_id, asset_id, local id) -> canonical id.
#:
#: Margret was merged across the two reels. Jonas's pickup mic deliberately was
#: not, so the legend shows it apart and the merge affordance has something to
#: act on.
SPEAKER_LINKS: dict[tuple[str, str, str], str] = {
    ("prj_harbour", PICKUP_ASSET, "T1"): "T1",
}


def _merge_speakers(project_id: str, asset_ids: list[str]) -> list[Speaker]:
    """Collapse per-asset voices into the merged view the UI groups by.

    The same rule the query layer applies: a merge a person made wins, and
    without one a voice is qualified by its reel — because two unmerged "Mic 2"s
    in one legend would read as one person.
    """
    first = asset_ids[0]
    merged: dict[str, Speaker] = {}
    for asset_id, local, default, label, confirmed, track, words, speech in SPEAKER_ROWS:
        if asset_id not in asset_ids:
            continue
        link = SPEAKER_LINKS.get((project_id, asset_id, local))
        cid = link or (local if asset_id == first else f"{local}@{asset_id}")
        existing = merged.get(cid)
        if existing is None:
            shown = default if (link or asset_id == first) else (
                f"{default} · {_ASSETS_BY_ID[asset_id].filename}"
            )
            merged[cid] = Speaker(
                id=cid, source="track", default_label=shown, label=label,
                confirmed=confirmed, track_index=track, word_count=words,
                speech_ms=speech, asset_ids=[asset_id],
            )
        else:
            existing.asset_ids.append(asset_id)
            existing.word_count += words
            existing.speech_ms += speech
            if not existing.label and label:
                existing.label = label
            existing.confirmed = existing.confirmed or confirmed
    return list(merged.values())


ATTRIBUTION = SpeakerAttribution(
    crosstalk_words=38,
    unattributed_words=4,
    reliable=True,
    notes=["38 words (5%) had two mics at similar levels — attributed to the louder one."],
)

def _transcript_asset(asset_id: str) -> TranscriptAsset:
    a = _ASSETS_BY_ID[asset_id]
    return TranscriptAsset(
        asset_id=a.id,
        filename=a.filename,
        rate=a.rate,
        drop_frame=a.drop_frame,
        start_tc_frames=a.start_tc_frames,
        duration_frames=a.duration_frames,
        language="en",
    )


def transcript_for(job_id: str) -> Transcript | None:
    """The transcript as a job sees it: only its own assets' beats.

    Beats belong to the asset and are reused across jobs for free; `used`,
    `order_idx`, `score` and `rationale` are the job's own opinion of them.
    """
    job = next((j for j in JOBS if j.id == job_id), None)
    if job is None:
        return None
    beats = [b for b in BEATS if b.asset_id in job.asset_ids]
    if not beats:
        return None
    speakers = _merge_speakers(job.project_id, job.asset_ids)
    order = 0
    scoped: list[Beat] = []
    for b in beats:
        copy = b.model_copy()
        if copy.used:
            copy.order_idx = order
            order += 1
        scoped.append(copy)
    return Transcript(
        job_id=job_id,
        assets=[_transcript_asset(a) for a in job.asset_ids],
        language="en",
        speakers=speakers,
        attribution=ATTRIBUTION.model_copy(update={"speakers": speakers}),
        beats=scoped,
        source_duration_frames=sum(
            _ASSETS_BY_ID[a].duration_frames for a in job.asset_ids
        ),
        cut_duration_frames=sum(
            b.end_frames - b.start_frames for b in scoped if b.used
        ),
    )


#: The whole project's merged view, for callers that are not job-scoped.
SPEAKERS = _merge_speakers("prj_harbour", [INTERVIEW_ASSET, PICKUP_ASSET])

TRANSCRIPT = transcript_for("job_2e57")

# ────────────────────────────────────────────────────────────────── projects
#
# Counts are derived, not typed in. A hand-written asset_count is a number that
# disagrees with the database the first time somebody adds a fixture, and then
# `use_mocks=True` and `use_mocks=False` render different screens — which is
# exactly what this file exists to prevent.


def _credits_used(project_id: str) -> float:
    return round(
        sum(-e.delta for e in LEDGER if e.project_id == project_id and e.kind == "settle"), 2
    )


def _project(id: str, name: str, created_at: str) -> Project:
    return Project(
        id=id,
        org_id=ORG.id,
        name=name,
        created_at=_dt(created_at),
        asset_count=sum(1 for a in ASSETS if a.project_id == id),
        job_count=sum(1 for j in JOBS if j.project_id == id),
        credits_used=_credits_used(id),
    )


# Newest first, which is the order the project list renders and the order the
# query returns. A fixture list in a different order to the API is a fixture
# that hides an ordering bug.
PROJECTS = sorted(
    [
        _project("prj_harbour", "Harbour Lights — Ep. 3", "2026-08-14T09:12:00"),
        _project("prj_summit", "Nordic Energy Summit", "2026-08-21T14:40:00"),
        _project("prj_field", "Field packages — August", "2026-08-03T07:55:00"),
        _project("prj_promo", "Q4 brand promo", "2026-08-26T16:20:00"),
    ],
    key=lambda p: p.created_at,
    reverse=True,
)
