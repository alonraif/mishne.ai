"""Load the fixtures into a development database.

    python -m mishne.db.seed --reset

Every mockup screen renders against the result, which is the point: a schema
that cannot reproduce the screens the product was designed around is a schema
with a hole in it, and this script is where that shows up.

Two connections, deliberately:

* `--reset` truncates as the **owner**, because the append-only tables refuse
  DELETE and the application role has no DELETE on them at all.
* Everything is inserted as the **application role**, inside a transaction with
  `app.org_id` set. So the seed passes through exactly the row-level security
  the API does, and a policy with a mistake in it fails here rather than in a
  test three weeks later.
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .. import mock
from ..config import get_settings
from ..pipeline.project import CACHE_VERSION
from .base import Base, normalise_url, session_for_org
from .models import ALL_TABLES

#: name -> Table, so inserts go through SQLAlchemy's typing. Passing a JSON
#: string into a jsonb column through raw SQL fails on the cast; passing a dict
#: through the Table does not.
TABLES = {t.name: t for t in Base.metadata.tables.values()}

PROVIDER = "xai"
PROVIDER_MODEL = "grok-stt"


def reset() -> None:
    """Empty every table, as the owner."""
    engine = sa.create_engine(normalise_url(get_settings().database_url))
    with engine.begin() as conn:
        conn.execute(sa.text(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE"))
    engine.dispose()


def _rows(session: Session, table: str, rows: list[dict]) -> None:
    if rows:
        session.execute(sa.insert(TABLES[table]), rows)


def seed() -> None:
    org = mock.ORG
    with session_for_org(org.id) as s:
        _rows(s, "orgs", [
            {"id": org.id, "name": org.name, "tier": org.tier,
             "retention_days": org.retention_days},
        ])
        _rows(s, "org_balances", [
            {"org_id": org.id, "available": org.credit_balance, "held": org.credits_held},
        ])
        _rows(s, "users", [
            {"id": mock.USER.id, "org_id": org.id, "email": mock.USER.email,
             "name": mock.USER.name, "role": mock.USER.role},
        ])

        _rows(s, "projects", [
            {"id": p.id, "org_id": org.id, "name": p.name,
             "created_by": mock.USER.id, "created_at": p.created_at}
            for p in mock.PROJECTS
        ])

        _rows(s, "assets", [
            {"id": a.id, "org_id": org.id, "project_id": a.project_id, "kind": a.kind,
             "ingest_mode": a.ingest_mode, "status": a.status, "filename": a.filename,
             "bytes": a.bytes,
             # Rational rate, in its two columns. Reassembling a Rate on read is
             # the only way the UI can format a timecode that exists.
             "edit_rate_num": a.rate.num, "edit_rate_den": a.rate.den,
             "drop_frame": a.drop_frame, "start_tc_frames": a.start_tc_frames,
             "duration_frames": a.duration_frames,
             # A seeded reel is playable, exactly as the fixture says it is
             # (`mock._transcript_asset`). Without these the columns fall to
             # their defaults — 'none' and '' — and the seeded database serves a
             # transcript whose player has nothing to play, while the fixtures
             # serve one that does. That is the drift `test_api_parity` exists
             # to catch, and it caught it. An AAF has no picture, so its preview
             # is sound only (ADR-0019, ADR-0020).
             "proxy_status": "ready",
             "proxy_kind": "audio" if a.kind == "aaf" else "video",
             "probe": {"codec": a.codec, "audio_tracks": a.audio_tracks},
             "created_at": a.uploaded_at}
            for a in mock.ASSETS
        ])

        # ── jobs, their assets and their steps ──────────────────────────────
        _rows(s, "jobs", [
            {"id": j.id, "org_id": org.id, "project_id": j.project_id,
             "name": j.name, "mode": j.mode,
             "status": j.status, "notes_raw": j.notes_raw,
             "brief": j.brief.model_dump(mode="json"),
             "estimate": j.estimate.model_dump(mode="json"),
             "approved_cap": j.estimate.cap,
             "credits_settled": j.credits_settled,
             "model_versions": _model_versions(j.status),
             "error": {"message": j.error} if j.error else None,
             "created_at": j.created_at, "finished_at": j.finished_at}
            for j in mock.JOBS
        ])
        _rows(s, "job_assets", [
            {"org_id": org.id, "job_id": j.id, "asset_id": asset_id, "order_idx": i}
            for j in mock.JOBS
            for i, asset_id in enumerate(j.asset_ids)
        ])
        _rows(s, "job_steps", [
            {"id": f"stp_{j.id}_{i:02d}", "org_id": org.id, "job_id": j.id, "idx": i,
             "name": step.name, "status": step.status, "detail": step.detail}
            for j in mock.JOBS
            for i, step in enumerate(j.steps)
        ])

        # ── transcripts, speakers, beats ────────────────────────────────────
        transcript_assets = [mock.INTERVIEW_ASSET, mock.PICKUP_ASSET]
        _rows(s, "transcripts", [
            {"id": f"trs_{asset_id}", "org_id": org.id, "asset_id": asset_id,
             "provider": PROVIDER, "provider_model": PROVIDER_MODEL, "language": "en",
             "ingest_version": CACHE_VERSION,
             "attribution": {
                 "crosstalk_words": mock.ATTRIBUTION.crosstalk_words,
                 "unattributed_words": mock.ATTRIBUTION.unattributed_words,
                 "reliable": mock.ATTRIBUTION.reliable,
                 "notes": mock.ATTRIBUTION.notes,
             }}
            for asset_id in transcript_assets
        ])

        # Speakers are per asset: "T1" on reel B is a different row from "T1" on
        # reel A, and stays a different person until somebody merges them.
        _rows(s, "speakers", [
            {"id": f"spk_{asset_id}_{local}", "org_id": org.id, "asset_id": asset_id,
             "speaker_id": local, "source": "track", "default_label": default,
             "label": label, "confirmed": confirmed, "track_index": track,
             "word_count": words, "speech_ms": speech}
            for asset_id, local, default, label, confirmed, track, words, speech
            in mock.SPEAKER_ROWS
        ])
        # The one merge a person actually made. Jonas's pickup mic is deliberately
        # left unmerged, so the legend shows it apart and the merge affordance has
        # something to act on.
        _rows(s, "speaker_links", [
            {"id": f"spl_{asset_id}_{local}", "org_id": org.id, "project_id": project_id,
             "canonical_speaker_id": canonical, "asset_id": asset_id,
             "speaker_id": local, "confirmed_by": mock.USER.id,
             "confirmed_at": mock.JOB_BY_ID["job_2e57"].created_at}
            for (project_id, asset_id, local), canonical in mock.SPEAKER_LINKS.items()
        ])

        _rows(s, "beats", [
            {"id": b.id, "org_id": org.id, "transcript_id": f"trs_{b.asset_id}",
             "asset_id": b.asset_id, "idx": b.idx,
             # Frames, in the asset's own rate. Never seconds.
             "start_frames": b.start_frames, "end_frames": b.end_frames,
             "speaker": _local_speaker(b.speaker), "text": b.text,
             "flags": b.flags}
            for b in mock.BEATS
        ])

        # ── each job's opinion of those beats ───────────────────────────────
        scores: list[dict] = []
        selections: list[dict] = []
        for job in mock.JOBS:
            beats = [b for b in mock.BEATS if b.asset_id in job.asset_ids]
            if not beats or job.status in ("analyzing", "failed"):
                continue
            order = 0
            for b in beats:
                scores.append({
                    "id": f"bsc_{job.id}_{b.id}", "org_id": org.id, "job_id": job.id,
                    "beat_id": b.id, "composite": b.score,
                    "scores": {"composite": b.score},
                    "rationale": b.rationale,
                })
                if b.used:
                    selections.append({
                        "id": f"sel_{job.id}_{order:03d}", "org_id": org.id,
                        "job_id": job.id, "beat_id": b.id, "asset_id": b.asset_id,
                        "order_idx": order,
                        "src_tc_in_frames": b.start_frames,
                        "src_tc_out_frames": b.end_frames,
                    })
                    order += 1
        _rows(s, "beat_scores", scores)
        _rows(s, "selections", selections)

        _rows(s, "artifacts", [
            {"id": a.id, "org_id": org.id, "job_id": a.job_id, "kind": a.kind,
             "filename": a.filename, "bytes": a.bytes, "validated": a.validated,
             "s3_key": f"artifacts/{a.job_id}/{a.filename}"}
            for a in mock.ARTIFACTS
        ])

        _rows(s, "credit_ledger", [
            {"id": e.id, "org_id": org.id, "project_id": e.project_id,
             "job_id": e.job_id if _job_exists(e.job_id) else None,
             "kind": e.kind, "delta": e.delta, "balance_after": e.balance_after,
             "description": e.description, "created_at": e.created_at}
            for e in mock.LEDGER
        ])


def _local_speaker(canonical: str) -> str:
    """The per-asset id a beat actually carries. "T2@ast_7c19" is a display id."""
    return canonical.split("@", 1)[0]


def _job_exists(job_id: str | None) -> bool:
    return job_id is not None and any(j.id == job_id for j in mock.JOBS)


def _model_versions(status: str) -> dict:
    """The reproducibility contract (ADR-0011): every model per task, in failover order."""
    if status in ("complete", "failed"):
        return {
            "transcribe": ["xai/grok-stt"],
            "brief": ["anthropic/claude-sonnet-4-5"],
            "score": ["anthropic/claude-sonnet-4-5", "openai/gpt-4.1"],
        }
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="truncate every table first")
    args = parser.parse_args()

    settings = get_settings()
    if settings.environment != "local":
        print(f"refusing to seed fixtures into environment={settings.environment!r}")
        return 2

    if args.reset:
        reset()
    seed()
    print(f"seeded {len(ALL_TABLES)} tables for {mock.ORG.id} ({mock.ORG.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
