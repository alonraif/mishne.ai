"""The read paths the web app needs, and nothing speculative.

Four screens drive everything here: the project list, a project with its assets
and jobs, a job with its steps, and the transcript page with its beats. Each is
one round trip; a list screen that issues a query per row is how a page that was
fast with three fixtures becomes unusable with three hundred rows.

Every function takes an already-org-scoped `Session` — one opened by
`session_for_org`, inside a transaction where `app.org_id` has been set. The
`org_id = :org` predicates below are therefore belt and braces: RLS has already
filtered the rows. They are kept because an index on `(org_id, ...)` is only
used if the query says so, and because a query that reads correctly on its own
is easier to review than one whose correctness lives in a policy elsewhere.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..schemas import (
    Artifact,
    Asset,
    Beat,
    CreditEstimate,
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
)
from .vocab import TARGET_NLE
from . import models as m


# ────────────────────────────────────────────────────────────────── projects


def list_projects(s: Session, org_id: str) -> list[Project]:
    """Project list with its three counters, in one query.

    `asset_count`, `job_count` and `credits_used` are aggregates, not stored
    columns: a stored counter is a number that drifts from the rows it claims to
    describe, and per-project spend already falls out of filtering the ledger by
    project (ADR-0006).
    """
    p = m.Project.__table__
    a = m.Asset.__table__
    j = m.Job.__table__
    lg = m.CreditLedger.__table__

    assets = (
        sa.select(sa.func.count())
        .select_from(a)
        .where(a.c.project_id == p.c.id)
        .scalar_subquery()
    )
    jobs = (
        sa.select(sa.func.count())
        .select_from(j)
        .where(j.c.project_id == p.c.id)
        .scalar_subquery()
    )
    credits = (
        sa.select(sa.func.coalesce(sa.func.sum(-lg.c.delta), 0))
        .select_from(lg)
        .where(lg.c.project_id == p.c.id, lg.c.kind == "settle")
        .scalar_subquery()
    )

    rows = s.execute(
        sa.select(
            p.c.id, p.c.org_id, p.c.name, p.c.created_at,
            assets.label("asset_count"),
            jobs.label("job_count"),
            credits.label("credits_used"),
        )
        .where(p.c.org_id == org_id, p.c.archived_at.is_(None))
        .order_by(p.c.created_at.desc())
    ).all()

    return [
        Project(
            id=r.id,
            org_id=r.org_id,
            name=r.name,
            created_at=r.created_at,
            asset_count=r.asset_count,
            job_count=r.job_count,
            credits_used=float(r.credits_used),
        )
        for r in rows
    ]


def get_project(s: Session, org_id: str, project_id: str) -> Project | None:
    return next((p for p in list_projects(s, org_id) if p.id == project_id), None)


# ──────────────────────────────────────────────────────────────────── assets


def _asset(row: sa.Row) -> Asset:
    probe = row.probe or {}
    return Asset(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        ingest_mode=row.ingest_mode,
        status=row.status,
        filename=row.filename,
        bytes=row.bytes,
        duration_frames=row.duration_frames,
        # Rational, reassembled from its two columns. There is no fps float
        # anywhere in this system and there should never be one.
        rate=Rate(num=row.edit_rate_num, den=row.edit_rate_den),
        drop_frame=row.drop_frame,
        start_tc_frames=row.start_tc_frames,
        codec=probe.get("codec", ""),
        audio_tracks=probe.get("audio_tracks", 0),
        uploaded_at=row.created_at,
    )


def list_assets(s: Session, org_id: str, project_id: str) -> list[Asset]:
    a = m.Asset.__table__
    rows = s.execute(
        sa.select(a)
        .where(a.c.org_id == org_id, a.c.project_id == project_id)
        .order_by(a.c.created_at)
    ).all()
    return [_asset(r) for r in rows]


def get_asset(s: Session, org_id: str, asset_id: str) -> Asset | None:
    a = m.Asset.__table__
    row = s.execute(sa.select(a).where(a.c.org_id == org_id, a.c.id == asset_id)).first()
    return _asset(row) if row else None


# ────────────────────────────────────────────────────────────────────── jobs


def _job(row: sa.Row, asset_ids: list[str], steps: list[JobStep]) -> Job:
    return Job(
        id=row.id,
        project_id=row.project_id,
        asset_ids=asset_ids,
        mode=row.mode,
        status=row.status,
        notes_raw=row.notes_raw,
        brief=EditBrief.model_validate(row.brief or {}),
        steps=steps,
        created_at=row.created_at,
        finished_at=row.finished_at,
        # The estimate as approved, read back rather than recomputed. Recomputing
        # against today's tier and balance answers a different question than
        # "what did the customer agree to?".
        estimate=CreditEstimate.model_validate(row.estimate),
        credits_settled=float(row.credits_settled) if row.credits_settled is not None else None,
        error=(row.error or {}).get("message") if row.error else None,
    )


def _steps_by_job(s: Session, org_id: str, job_ids: list[str]) -> dict[str, list[JobStep]]:
    if not job_ids:
        return {}
    st = m.JobStep.__table__
    rows = s.execute(
        sa.select(st)
        .where(st.c.org_id == org_id, st.c.job_id.in_(job_ids))
        .order_by(st.c.job_id, st.c.idx)
    ).all()
    out: dict[str, list[JobStep]] = {j: [] for j in job_ids}
    for r in rows:
        out[r.job_id].append(
            JobStep(
                name=r.name,
                # A display string derived from the step name, not stored: it is
                # copy, and copy that lives in rows is copy nobody can change.
                label=_STEP_LABELS.get(r.name, r.name),
                status=r.status,
                started_at=r.started_at,
                finished_at=r.finished_at,
                detail=r.detail,
            )
        )
    return out


def _assets_by_job(s: Session, org_id: str, job_ids: list[str]) -> dict[str, list[str]]:
    if not job_ids:
        return {}
    ja = m.JobAsset.__table__
    rows = s.execute(
        sa.select(ja.c.job_id, ja.c.asset_id)
        .where(ja.c.org_id == org_id, ja.c.job_id.in_(job_ids))
        .order_by(ja.c.job_id, ja.c.order_idx)
    ).all()
    out: dict[str, list[str]] = {j: [] for j in job_ids}
    for r in rows:
        out[r.job_id].append(r.asset_id)
    return out


def list_jobs(s: Session, org_id: str, project_id: str) -> list[Job]:
    j = m.Job.__table__
    rows = s.execute(
        sa.select(j)
        .where(j.c.org_id == org_id, j.c.project_id == project_id)
        .order_by(j.c.created_at.desc())
    ).all()
    ids = [r.id for r in rows]
    steps = _steps_by_job(s, org_id, ids)
    assets = _assets_by_job(s, org_id, ids)
    return [_job(r, assets.get(r.id, []), steps.get(r.id, [])) for r in rows]


def get_job(s: Session, org_id: str, job_id: str) -> Job | None:
    j = m.Job.__table__
    row = s.execute(sa.select(j).where(j.c.org_id == org_id, j.c.id == job_id)).first()
    if row is None:
        return None
    steps = _steps_by_job(s, org_id, [job_id])
    assets = _assets_by_job(s, org_id, [job_id])
    return _job(row, assets.get(job_id, []), steps.get(job_id, []))


def list_artifacts(s: Session, org_id: str, job_id: str) -> list[Artifact]:
    ar = m.Artifact.__table__
    # Delivery order, not alphabetical: the AAF is what an Avid house came for
    # and it belongs at the top; the EDL is the fallback nobody opens first.
    preference = sa.case(
        {kind: i for i, kind in enumerate(("aaf", "fcpxml", "edl", "otio", "json"))},
        value=ar.c.kind,
        else_=99,
    )
    rows = s.execute(
        sa.select(ar).where(ar.c.org_id == org_id, ar.c.job_id == job_id).order_by(preference)
    ).all()
    return [
        Artifact(
            id=r.id,
            job_id=r.job_id,
            kind=r.kind,
            filename=r.filename,
            bytes=r.bytes,
            validated=r.validated,
            target_nle=TARGET_NLE.get(r.kind, ""),
        )
        for r in rows
    ]


# ────────────────────────────────────────────────────────────────── transcript


def _canonical(local: str, asset_id: str, first_asset: str, link: str | None) -> str:
    """The id the UI groups a voice by.

    A merge is a row a person created, and it wins. Without one, two reels'
    "T1" are two different people — so the id is qualified by its reel, which is
    what makes the legend show them apart rather than silently merging them.
    """
    if link:
        return link
    return local if asset_id == first_asset else f"{local}@{asset_id}"


def get_transcript(s: Session, org_id: str, job_id: str) -> Transcript | None:
    """The job's transcript, assembled across every asset it draws on.

    Transcripts are keyed on the asset, not the job (ADR-0008) — this is the
    join that presents several of them as one page. `used`, `order_idx`, `score`
    and `rationale` are the job's opinion of beats it does not own.
    """
    job = get_job(s, org_id, job_id)
    if job is None or not job.asset_ids:
        return None

    a = m.Asset.__table__
    t = m.Transcript.__table__
    b = m.Beat.__table__
    sp = m.Speaker.__table__
    sl = m.SpeakerLink.__table__
    bs = m.BeatScore.__table__
    sel = m.Selection.__table__

    order = {asset_id: i for i, asset_id in enumerate(job.asset_ids)}
    first_asset = job.asset_ids[0]

    asset_rows = s.execute(
        sa.select(a).where(a.c.org_id == org_id, a.c.id.in_(job.asset_ids))
    ).all()
    assets = {r.id: r for r in asset_rows}

    transcripts = s.execute(
        sa.select(t.c.id, t.c.asset_id, t.c.language, t.c.attribution).where(
            t.c.org_id == org_id, t.c.asset_id.in_(job.asset_ids)
        )
    ).all()
    if not transcripts:
        return None

    # ── speakers, with the merge applied ────────────────────────────────────
    speaker_rows = s.execute(
        sa.select(
            sp.c.asset_id, sp.c.speaker_id, sp.c.source, sp.c.default_label,
            sp.c.label, sp.c.confirmed, sp.c.track_index, sp.c.word_count,
            sp.c.speech_ms, sl.c.canonical_speaker_id,
        )
        .select_from(
            sp.outerjoin(
                sl,
                sa.and_(
                    sl.c.asset_id == sp.c.asset_id,
                    sl.c.speaker_id == sp.c.speaker_id,
                    sl.c.project_id == job.project_id,
                ),
            )
        )
        .where(sp.c.org_id == org_id, sp.c.asset_id.in_(job.asset_ids))
    ).all()

    merged: dict[str, Speaker] = {}
    canonical_of: dict[tuple[str, str], str] = {}
    for r in sorted(speaker_rows, key=lambda r: (order[r.asset_id], r.speaker_id)):
        cid = _canonical(r.speaker_id, r.asset_id, first_asset, r.canonical_speaker_id)
        canonical_of[(r.asset_id, r.speaker_id)] = cid
        existing = merged.get(cid)
        if existing is None:
            # An unmerged voice on a later reel is suffixed with that reel: two
            # unnamed "Mic 2"s in one legend would read as one person, which is
            # the mistake speaker_links exists to make impossible.
            default_label = r.default_label
            if not r.canonical_speaker_id and r.asset_id != first_asset:
                default_label = f"{r.default_label} · {assets[r.asset_id].filename}"
            merged[cid] = Speaker(
                id=cid,
                source=r.source,
                default_label=default_label,
                label=r.label,
                confirmed=r.confirmed,
                track_index=r.track_index,
                word_count=r.word_count,
                speech_ms=r.speech_ms,
                asset_ids=[r.asset_id],
            )
        else:
            # A merged voice: totals add up, and a name confirmed on either reel
            # is the name.
            existing.asset_ids.append(r.asset_id)
            existing.word_count += r.word_count
            existing.speech_ms += r.speech_ms
            if not existing.label and r.label:
                existing.label = r.label
            existing.confirmed = existing.confirmed or r.confirmed

    # ── beats, with this job's scores and selections ────────────────────────
    beat_rows = s.execute(
        sa.select(
            b.c.id, b.c.idx, b.c.asset_id, b.c.speaker, b.c.start_frames,
            b.c.end_frames, b.c.text, b.c.flags,
            bs.c.composite, bs.c.rationale,
            sel.c.order_idx,
        )
        .select_from(
            b.outerjoin(bs, sa.and_(bs.c.beat_id == b.c.id, bs.c.job_id == job_id))
            .outerjoin(sel, sa.and_(sel.c.beat_id == b.c.id, sel.c.job_id == job_id))
        )
        .where(b.c.org_id == org_id, b.c.asset_id.in_(job.asset_ids))
    ).all()

    beats = [
        Beat(
            id=r.id,
            idx=r.idx,
            asset_id=r.asset_id,
            speaker=canonical_of.get((r.asset_id, r.speaker), r.speaker),
            start_frames=r.start_frames,
            end_frames=r.end_frames,
            text=r.text,
            flags=list(r.flags or []),
            used=r.order_idx is not None,
            order_idx=r.order_idx,
            score=r.composite,
            rationale=r.rationale,
        )
        for r in sorted(beat_rows, key=lambda r: (order[r.asset_id], r.idx))
    ]

    first = next((t for t in transcripts if t.asset_id == first_asset), transcripts[0])
    attribution = SpeakerAttribution.model_validate(first.attribution or {})
    attribution.speakers = list(merged.values())

    return Transcript(
        job_id=job_id,
        assets=[
            TranscriptAsset(
                asset_id=r.asset_id,
                filename=assets[r.asset_id].filename,
                rate=Rate(
                    num=assets[r.asset_id].edit_rate_num,
                    den=assets[r.asset_id].edit_rate_den,
                ),
                drop_frame=assets[r.asset_id].drop_frame,
                start_tc_frames=assets[r.asset_id].start_tc_frames,
                duration_frames=assets[r.asset_id].duration_frames,
                language=r.language,
            )
            for r in sorted(transcripts, key=lambda r: order[r.asset_id])
        ],
        language=first.language,
        speakers=list(merged.values()),
        attribution=attribution,
        beats=beats,
        source_duration_frames=sum(assets[i].duration_frames for i in job.asset_ids),
        cut_duration_frames=sum(b.end_frames - b.start_frames for b in beats if b.used),
    )


# ─────────────────────────────────────────────────────────────────── billing


def get_org(s: Session, org_id: str) -> Org | None:
    o = m.Org.__table__
    bal = m.OrgBalance.__table__
    row = s.execute(
        sa.select(o.c.id, o.c.name, o.c.tier, o.c.retention_days, bal.c.available, bal.c.held)
        .select_from(o.outerjoin(bal, bal.c.org_id == o.c.id))
        .where(o.c.id == org_id)
    ).first()
    if row is None:
        return None
    return Org(
        id=row.id,
        name=row.name,
        tier=row.tier,
        credit_balance=float(row.available or 0),
        credits_held=float(row.held or 0),
        retention_days=row.retention_days,
    )


def list_ledger(s: Session, org_id: str, project_id: str | None = None) -> list[LedgerEntry]:
    lg = m.CreditLedger.__table__
    q = sa.select(lg).where(lg.c.org_id == org_id)
    if project_id:
        q = q.where(lg.c.project_id == project_id)
    rows = s.execute(q.order_by(lg.c.created_at.desc())).all()
    return [
        LedgerEntry(
            id=r.id,
            org_id=r.org_id,
            project_id=r.project_id,
            job_id=r.job_id,
            kind=r.kind,
            delta=float(r.delta),
            balance_after=float(r.balance_after),
            description=r.description,
            created_at=r.created_at,
        )
        for r in rows
    ]


def _step_labels() -> dict[str, str]:
    from ..pipeline import STEPS

    return {step.name: step.label for step in STEPS}


_STEP_LABELS = _step_labels()
