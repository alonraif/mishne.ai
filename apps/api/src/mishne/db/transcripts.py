"""Writing what the pipeline produced into the tables the app reads.

## Why this file exists

`repository.get_transcript` has always assembled a job's transcript out of
`transcripts`, `speakers`, `speaker_links`, `beats`, `beat_scores` and
`selections`. **Nothing wrote any of them.** The only rows those tables have
ever held came from `db/seed.py`, which is why every test passed: the read path
was exercised against seeded data and the write path did not exist.

The symptom was invisible while the web app rendered fixtures. It stops being
invisible the moment the screens read the API: `GET /v1/jobs/{id}/transcript`
returns 404 for every job the worker has actually run, and the cut editor has
nothing to edit.

## What is written where

**Per asset, once** — transcripts, speakers, beats. Transcription belongs to the
upload, not the job (ADR-0008): a second job over the same asset must find these
rows already there and add nothing.

**Per job** — beat_scores and selections. These are one job's *opinion* of beats
it does not own: what it thought of each, and which it cut.

## Frames, not milliseconds

The pipeline works in milliseconds from the start of the media; every row here
is in frames at the asset's own rate, offset by its start timecode, because that
is what the beat's timecode means to an editor and what `Cut.src_in` already
uses. Converting at the boundary keeps one origin in the database.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..logging import get_logger
from ..timecode import Rate, ms_to_frames
from . import models as m

log = get_logger(__name__)


def _frames(ms: int, rate: Rate, start_tc_frames: int) -> int:
    """Media-relative milliseconds to absolute source timecode frames."""
    return start_tc_frames + ms_to_frames(ms, rate)


def record_asset(
    s: Session,
    org_id: str,
    ingest,
    *,
    ingest_version: int,
    raw_s3_key: str = "",
) -> str:
    """The transcript, speakers and beats for one upload. Returns its id.

    Idempotent, and it has to be: the ingest cache means a second job over the
    same asset re-runs this with identical content (ADR-0008), and a retried
    step re-runs it with identical content too.

    A beat is never deleted while a cut references it — the foreign key from
    `selections` is RESTRICT and it is right: removing a beat some job cut on
    would rewrite that job's history. When re-ingestion under a new
    `ingest_version` produces different beats, the unreferenced old ones go and
    the referenced ones stay, which leaves the earlier job readable and the new
    one correct.
    """
    asset_id = ingest.asset_id
    transcript_id = f"trs_{asset_id}"
    rate, start_tc = ingest.rate, ingest.start_tc_frames

    existing = s.execute(
        sa.select(m.Transcript.__table__.c.ingest_version).where(
            m.Transcript.__table__.c.org_id == org_id,
            m.Transcript.__table__.c.id == transcript_id,
        )
    ).scalar_one_or_none()

    attribution = getattr(ingest, "attribution", None)
    s.execute(
        pg_insert(m.Transcript.__table__)
        .values(
            id=transcript_id,
            org_id=org_id,
            asset_id=asset_id,
            # Empty when the ingest came from a cache written before the engine
            # was recorded. Empty is honest; a guess would not be.
            provider=getattr(ingest, "asr_provider", "") or "",
            provider_model=getattr(ingest, "asr_model", "") or "",
            language=ingest.language or "",
            raw_s3_key=raw_s3_key or None,
            ingest_version=ingest_version,
            attribution={
                "crosstalk_words": getattr(attribution, "crosstalk_words", 0),
                "unattributed_words": getattr(attribution, "unattributed_words", 0),
                "reliable": getattr(attribution, "reliable", True),
                "notes": list(getattr(attribution, "notes", []) or []),
            },
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "provider": sa.text("excluded.provider"),
                "provider_model": sa.text("excluded.provider_model"),
                "language": sa.text("excluded.language"),
                "ingest_version": sa.text("excluded.ingest_version"),
                "attribution": sa.text("excluded.attribution"),
            },
        )
    )

    if existing is not None and existing != ingest_version:
        _drop_unreferenced_beats(s, org_id, transcript_id)

    for speaker in ingest.speakers:
        s.execute(
            pg_insert(m.Speaker.__table__)
            .values(
                id=f"spk_{asset_id}_{speaker.id}",
                org_id=org_id,
                asset_id=asset_id,
                speaker_id=speaker.id,
                source=speaker.source,
                default_label=speaker.default_label,
                # `label` and `confirmed` are a person's, not the pipeline's.
                # Re-running ingest must not unname a speaker somebody named, so
                # they are set on insert and never on update.
                label=speaker.label or "",
                confirmed=bool(speaker.confirmed),
                track_index=speaker.track_index,
                word_count=speaker.word_count,
                speech_ms=speaker.speech_ms,
            )
            .on_conflict_do_update(
                index_elements=["asset_id", "speaker_id"],
                set_={
                    "source": sa.text("excluded.source"),
                    "default_label": sa.text("excluded.default_label"),
                    "track_index": sa.text("excluded.track_index"),
                    "word_count": sa.text("excluded.word_count"),
                    "speech_ms": sa.text("excluded.speech_ms"),
                },
            )
        )

    for beat in ingest.beats:
        start = _frames(beat.start_ms, rate, start_tc)
        end = _frames(beat.end_ms, rate, start_tc)
        if end <= start:
            # `ck_beats_positive_duration`. A beat shorter than a frame is a
            # segmentation artefact, and inserting it fails the whole job at the
            # very end, after the artifacts have been published.
            end = start + 1
        s.execute(
            pg_insert(m.Beat.__table__)
            .values(
                id=beat.id,
                org_id=org_id,
                transcript_id=transcript_id,
                asset_id=asset_id,
                idx=beat.idx,
                start_frames=start,
                end_frames=end,
                # The asset-local speaker id ("T1"), which is what
                # `speaker_links` and the merge in `repository` key on. Not the
                # display name — that belongs to the speaker row.
                speaker=beat.speaker or "",
                text=beat.text,
                flags=list(beat.flags or []),
                mean_confidence=float(getattr(beat, "mean_confidence", 1.0)),
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "idx": sa.text("excluded.idx"),
                    "start_frames": sa.text("excluded.start_frames"),
                    "end_frames": sa.text("excluded.end_frames"),
                    "speaker": sa.text("excluded.speaker"),
                    "text": sa.text("excluded.text"),
                    "flags": sa.text("excluded.flags"),
                    "mean_confidence": sa.text("excluded.mean_confidence"),
                },
            )
        )

    log.info("transcript.recorded", asset_id=asset_id, beats=len(ingest.beats),
             speakers=len(ingest.speakers))
    return transcript_id


def _drop_unreferenced_beats(s: Session, org_id: str, transcript_id: str) -> None:
    beats, selections = m.Beat.__table__, m.Selection.__table__
    referenced = sa.select(selections.c.beat_id).where(selections.c.org_id == org_id)
    result = s.execute(
        sa.delete(beats).where(
            beats.c.org_id == org_id,
            beats.c.transcript_id == transcript_id,
            beats.c.id.notin_(referenced),
        )
    )
    if result.rowcount:
        log.info("transcript.reingested", transcript_id=transcript_id,
                 dropped=result.rowcount)


def record_job_view(
    s: Session,
    org_id: str,
    job_id: str,
    *,
    candidates: list,
    scores: dict,
    cuts: list,
) -> tuple[int, int]:
    """One job's scores and its cut. Returns (scored beats, selected spans).

    **Scores are recorded per beat, not per candidate span.** Stage 6 carves a
    long beat into several candidates and stage 7 scores each of them, so there
    are more scores than beats; `beat_scores` is unique on (job, beat) and the
    transcript page shows one number per beat. The number kept is the best
    candidate's, with its rationale, because that is what the beat was worth to
    this job — and the count of candidates is kept beside it so a low number
    that came from one of eleven attempts does not read like a considered
    verdict on the whole beat.

    **Selections are per span**, which is why a beat can appear twice: a beat
    split into two spans with the middle dropped is two clips in the timeline.
    Migration 0007 removed the unique constraint that forbade it.
    """
    _clear_job_view(s, org_id, job_id)

    best: dict[str, tuple[float, object]] = {}
    counts: dict[str, int] = {}
    for candidate in candidates:
        parent = getattr(candidate, "parent_id", "") or candidate.id
        counts[parent] = counts.get(parent, 0) + 1
        value = float(scores.get(candidate.id, 0.0))
        if parent not in best or value > best[parent][0]:
            best[parent] = (value, candidate)

    for parent_id, (value, candidate) in best.items():
        s.execute(
            sa.insert(m.BeatScore.__table__).values(
                id=f"bsc_{job_id}_{parent_id}",
                org_id=org_id,
                job_id=job_id,
                beat_id=parent_id,
                composite=value,
                scores={"composite": value, "candidates": counts[parent_id]},
                rationale=getattr(candidate, "rationale", "") or None,
                depends_on=list(getattr(candidate, "depends_on", []) or []),
            )
        )

    for order_idx, cut in enumerate(cuts):
        s.execute(
            sa.insert(m.Selection.__table__).values(
                id=f"sel_{job_id}_{order_idx:03d}",
                org_id=org_id,
                job_id=job_id,
                # The beat, not the candidate span: candidates are per job and
                # are not rows, and this column is a foreign key into `beats`.
                beat_id=getattr(cut, "parent_id", "") or cut.beat_id,
                asset_id=cut.asset_id,
                order_idx=order_idx,
                # Post-refine frames: where the cut actually is, after silence
                # snapping and handles. The span stage 8 chose is not what an
                # editor will see on the timeline, and this table is the record
                # of the cut rather than of the intention.
                src_tc_in_frames=cut.src_in,
                src_tc_out_frames=cut.src_out,
            )
        )

    log.info("job.view_recorded", job_id=job_id, scored=len(best),
             selected=len(cuts))
    return len(best), len(cuts)


def _clear_job_view(s: Session, org_id: str, job_id: str) -> None:
    """A re-run replaces this job's opinion rather than adding to it.

    Deleting is safe here in a way it is not for beats: these rows belong to
    this job and nothing references them.
    """
    for table in (m.Selection.__table__, m.BeatScore.__table__):
        s.execute(
            sa.delete(table).where(
                table.c.org_id == org_id, table.c.job_id == job_id
            )
        )
