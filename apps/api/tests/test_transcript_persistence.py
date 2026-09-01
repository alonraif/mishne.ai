"""The pipeline's output reaches the tables the API reads.

`repository.get_transcript` has existed since B1 and had no writer: every row
those tables held came from `db/seed.py`. The read path was tested against
seeded data, so nothing failed — while `GET /v1/jobs/{id}/transcript` returned
404 for every job the worker had actually run.

Five properties here, and each is a way that was allowed to be true:

* the rows appear at all, in frames rather than milliseconds — **and under the
  asset row rather than the content digest the pipeline works in.** The two
  were the same string in this file and are never the same string in the
  product, so every insert the worker made failed a foreign key, the error was
  swallowed by design, and the cut editor opened empty on every job that had
  really run;
* running the same ingest twice adds nothing (ADR-0008 makes that the normal
  case, not an edge one);
* a speaker a person named stays named across a re-ingest;
* **a beat carved into two selected spans is two clips.** ADR-0010 says stage 6
  carves candidates out of one beat and the solver may take two of them. The
  schema forbade it with UNIQUE (job_id, beat_id) until migration 0007, and the
  first real cut that did it would have raised at the very last write of the
  job — after the artifacts were published and the customer was charged.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

from mishne.timecode import Rate  # noqa: E402

ASSET = "ast_transcript_test"
JOB = "job_transcript_test"
#: What the *pipeline* calls the same upload: the digest of its bytes, not the
#: row id (`db/ids.py`). They were the same string in this file once, and that
#: is precisely why the writer could put content ids into columns that are
#: foreign keys into `assets` and no test noticed.
PIPELINE = f"a_{'d4' * 12}"
ASSETS = {PIPELINE: ASSET}


def _p(idx: int) -> str:
    """A beat as the pipeline names it: its asset's content id, then its index."""
    return f"{PIPELINE}_beat_{idx:04d}"


def _d(idx: int) -> str:
    """The same beat as the database must name it: under its asset row."""
    return f"{ASSET}_beat_{idx:04d}"

RATE = Rate(25, 1, False)
#: 10:00:00:00 at 25 fps. Beats are stored against the asset's own timecode
#: origin, so a beat 2s into the media is not frame 50.
START_TC = 900_000


# ── the shapes the writer reads, without importing the whole pipeline ──────


@dataclass
class FakeSpeaker:
    id: str
    source: str = "track"
    default_label: str = "Mic 1"
    label: str = ""
    confirmed: bool = False
    track_index: int | None = 1
    word_count: int = 100
    speech_ms: int = 60_000


@dataclass
class FakeBeat:
    id: str
    idx: int
    start_ms: int
    end_ms: int
    text: str
    speaker: str = "T1"
    flags: list = field(default_factory=list)
    mean_confidence: float = 0.9
    parent_id: str = ""
    rationale: str = ""
    depends_on: list = field(default_factory=list)

    def __post_init__(self):
        if not self.parent_id:
            self.parent_id = self.id


@dataclass
class FakeAttribution:
    crosstalk_words: int = 3
    unattributed_words: int = 1
    reliable: bool = True
    notes: list = field(default_factory=list)


@dataclass
class FakeIngest:
    asset_id: str = PIPELINE
    rate: Rate = RATE
    start_tc_frames: int = START_TC
    language: str = "en"
    beats: list = field(default_factory=list)
    speakers: list = field(default_factory=list)
    attribution: FakeAttribution = field(default_factory=FakeAttribution)
    asr_provider: str = "xai"
    asr_model: str = "grok-stt"


@dataclass
class FakeCut:
    beat_id: str
    parent_id: str
    asset_id: str
    src_in: int
    src_out: int


def _ingest() -> FakeIngest:
    return FakeIngest(
        beats=[
            FakeBeat(_p(0), 0, 2_000, 6_000, "The harbour is being closed."),
            FakeBeat(_p(1), 1, 6_400, 20_000, "A long answer with room inside it."),
        ],
        speakers=[FakeSpeaker(id="T1")],
    )


@pytest.fixture
def asset_and_job(tenant, owner):
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                "duration_frames, start_tc_frames, probe, probed_at) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'ready', 'rushes.mov', 1024, "
                ":c, 25, 1, 90000, :tc, '{}'::jsonb, now())"
            ),
            {"a": ASSET, "o": ORG, "p": PROJECT, "c": "e" * 64, "tc": START_TC},
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'complete', 'ai', '', :brief, :estimate, 10)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT,
             # A brief and an estimate the schema will validate:
             # `repository.get_job` parses both columns, so an empty object is
             # not a neutral placeholder.
             "brief": '{"target_duration_s": 360}',
             "estimate": (
                 '{"mode": "ai", "source_duration_frames": 90000, '
                 '"source_hours": 1.0, "lines": [], "subtotal": 10, '
                 '"cap": 10, "balance_before": 500, "balance_after": 490, '
                 '"sufficient": true, "shortfall": 0}'
             )},
        )
        conn.execute(
            sa.text(
                "INSERT INTO job_assets (org_id, job_id, asset_id, order_idx) "
                "VALUES (:o, :j, :a, 0)"
            ),
            {"o": ORG, "j": JOB, "a": ASSET},
        )
    yield
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def _session(owner):
    from sqlalchemy.orm import Session

    return Session(owner)


# ── the asset half ────────────────────────────────────────────────────────


@requires_schema
def test_the_beats_arrive_in_frames_against_the_assets_own_timecode(
    owner, asset_and_job
):
    from mishne.db import transcripts as writes

    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        s.commit()

    rows = _beats(owner)
    # Named after the asset ROW. Under the pipeline's own ids this insert fails
    # `beats_asset_id_fkey` and the whole transcript is silently lost.
    assert [r.id for r in rows] == [_d(0), _d(1)]
    assert [r.asset_id for r in rows] == [ASSET, ASSET]
    # 2.000s at 25 fps is 50 frames, offset by the asset's start timecode. A
    # writer that forgot the offset produces a transcript whose timecodes are
    # ten hours out and look plausible.
    assert rows[0].start_frames == START_TC + 50
    assert rows[0].end_frames == START_TC + 150
    assert rows[0].speaker == "T1"


@requires_schema
def test_recording_the_same_ingest_twice_changes_nothing(owner, asset_and_job):
    """The ingest cache means this is the ordinary case: a second job over the
    same upload re-runs the writer with identical content (ADR-0008)."""
    from mishne.db import transcripts as writes

    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        s.commit()

    assert len(_beats(owner)) == 2
    with owner.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM transcripts WHERE org_id = :o"), {"o": ORG}
        ).scalar() == 1


@requires_schema
def test_a_re_ingest_does_not_unname_a_speaker_a_person_named(owner, asset_and_job):
    """`label` and `confirmed` belong to a human. The pipeline knows which
    microphone a voice came down and nothing about whose voice it is."""
    from mishne.db import transcripts as writes

    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        s.commit()
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "UPDATE speakers SET label = 'Margret Olsen', confirmed = true "
                "WHERE org_id = :o"
            ),
            {"o": ORG},
        )
    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=8)
        s.commit()

    with owner.connect() as conn:
        row = conn.execute(
            sa.text("SELECT label, confirmed FROM speakers WHERE org_id = :o"),
            {"o": ORG},
        ).one()
    assert row.label == "Margret Olsen" and row.confirmed is True


# ── the job half ──────────────────────────────────────────────────────────


@requires_schema
def test_a_beat_carved_into_two_selected_spans_is_two_clips(owner, asset_and_job):
    """The case migration 0007 exists for.

    Stage 6 carves `b2` into two candidates, the solver takes both and drops
    the middle. That is two clips on the timeline out of one beat, and until
    0007 the second insert raised on UNIQUE (job_id, beat_id) — at the very
    last write of a job whose artifacts were already published.
    """
    from mishne.db import transcripts as writes

    candidates = [
        FakeBeat(_p(0), 0, 2_000, 6_000, "…", rationale="Sharp."),
        FakeBeat(f"{_p(1)}#a", 1, 6_400, 11_000, "…", parent_id=_p(1),
                 rationale="Good half"),
        FakeBeat(f"{_p(1)}#b", 2, 15_000, 20_000, "…", parent_id=_p(1),
                 rationale="Better half"),
    ]
    cuts = [
        FakeCut(_p(0), _p(0), PIPELINE, START_TC + 50, START_TC + 150),
        FakeCut(f"{_p(1)}#a", _p(1), PIPELINE, START_TC + 160, START_TC + 275),
        FakeCut(f"{_p(1)}#b", _p(1), PIPELINE, START_TC + 375, START_TC + 500),
    ]

    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        writes.record_job_view(
            s, ORG, JOB, assets=ASSETS,
            candidates=candidates,
            scores={_p(0): 90.0, f"{_p(1)}#a": 40.0, f"{_p(1)}#b": 71.0},
            cuts=cuts,
        )
        s.commit()

    with owner.connect() as conn:
        selections = conn.execute(
            sa.text(
                "SELECT beat_id, asset_id, order_idx FROM selections "
                "WHERE org_id = :o ORDER BY order_idx"
            ),
            {"o": ORG},
        ).all()
    assert [r.beat_id for r in selections] == [_d(0), _d(1), _d(1)]
    assert {r.asset_id for r in selections} == {ASSET}
    assert [r.order_idx for r in selections] == [0, 1, 2]


@requires_schema
def test_a_beat_keeps_its_best_candidates_score(owner, asset_and_job):
    """Scores are per beat and candidates are per span, so something has to
    choose. The best is what the beat was worth to this job; the count of
    candidates is kept beside it so a low score from one of three attempts does
    not read as a verdict on the whole beat."""
    from mishne.db import transcripts as writes

    candidates = [
        FakeBeat(f"{_p(1)}#a", 1, 6_400, 11_000, "…", parent_id=_p(1),
                 rationale="Good half"),
        FakeBeat(f"{_p(1)}#b", 2, 15_000, 20_000, "…", parent_id=_p(1),
                 rationale="Better half"),
    ]
    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        writes.record_job_view(
            s, ORG, JOB, assets=ASSETS, candidates=candidates,
            scores={f"{_p(1)}#a": 40.0, f"{_p(1)}#b": 71.0}, cuts=[],
        )
        s.commit()

    with owner.connect() as conn:
        row = conn.execute(
            sa.text(
                "SELECT beat_id, composite, rationale, scores FROM beat_scores "
                "WHERE org_id = :o"
            ),
            {"o": ORG},
        ).one()
    assert row.beat_id == _d(1)
    assert row.composite == pytest.approx(71.0)
    assert row.rationale == "Better half"
    assert row.scores["candidates"] == 2


@requires_schema
def test_re_running_a_job_replaces_its_opinion_rather_than_doubling_it(
    owner, asset_and_job
):
    from mishne.db import transcripts as writes

    cuts = [FakeCut(_p(0), _p(0), PIPELINE, START_TC + 50, START_TC + 150)]
    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        for _ in range(2):
            writes.record_job_view(
                s, ORG, JOB, assets=ASSETS,
                candidates=[FakeBeat(_p(0), 0, 2_000, 6_000, "…")],
                scores={_p(0): 90.0}, cuts=cuts,
            )
        s.commit()

    with owner.connect() as conn:
        assert conn.execute(
            sa.text("SELECT count(*) FROM selections WHERE org_id = :o"), {"o": ORG}
        ).scalar() == 1


# ── and the read path the API actually serves ─────────────────────────────


@requires_schema
def test_the_api_reads_back_one_beat_per_beat(owner, asset_and_job):
    """A beat cut twice must not appear twice on the transcript page.

    `Beat.orderIdx` in the contract is beat-level, so the join has to aggregate
    — otherwise the reader sees the same paragraph of text twice, at two
    positions, and no amount of reading the page explains why.
    """
    from mishne.db import repository, transcripts as writes

    candidates = [
        FakeBeat(_p(0), 0, 2_000, 6_000, "…"),
        FakeBeat(f"{_p(1)}#a", 1, 6_400, 11_000, "…", parent_id=_p(1)),
        FakeBeat(f"{_p(1)}#b", 2, 15_000, 20_000, "…", parent_id=_p(1)),
    ]
    cuts = [
        FakeCut(f"{_p(1)}#a", _p(1), PIPELINE, START_TC + 160, START_TC + 275),
        FakeCut(f"{_p(1)}#b", _p(1), PIPELINE, START_TC + 375, START_TC + 500),
    ]
    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        writes.record_job_view(
            s, ORG, JOB, assets=ASSETS, candidates=candidates,
            scores={_p(0): 10.0, f"{_p(1)}#a": 40.0,
                    f"{_p(1)}#b": 71.0}, cuts=cuts,
        )
        s.commit()

    with _session(owner) as s:
        transcript = repository.get_transcript(s, ORG, JOB)

    assert transcript is not None
    assert [b.id for b in transcript.beats] == [_d(0), _d(1)]
    cut_beat = next(b for b in transcript.beats if b.id == _d(1))
    assert cut_beat.used is True
    # The first position it appears at. The timeline is the record of the rest.
    assert cut_beat.order_idx == 0
    assert next(b for b in transcript.beats if b.id == _d(0)).used is False


# ── the two id spaces ─────────────────────────────────────────────────────


def test_a_beat_id_survives_the_round_trip_through_the_database():
    """The resume path. A person marks the transcript, the browser sends back
    database beat ids, and stage 8 has to find those beats among the ones the
    ingest cache just handed back under their content names. If the two
    translations are not inverses, a manual job fails at stage 8 with "not a
    beat of any asset in this job" — after the person has done the work."""
    from mishne.db import ids

    for beat_id in (_p(0), f"{_p(1)}#a", "beat_0007"):
        assert ids.pipeline_id(ids.db_id(beat_id, ASSETS), ASSETS) == beat_id


@requires_schema
def test_the_same_footage_in_two_rows_keeps_two_sets_of_beats(
    owner, asset_and_job, second_row
):
    """The same interview uploaded twice is two asset rows and one ingest.

    Beat ids are content-addressed, so both rows produce the identical list.
    `beats.id` is a primary key and the write is an upsert: unnamespaced, the
    second recording would take the first row's beats and move them to its own
    asset, and a delivered job's transcript would empty out with no error
    anywhere. Namespacing on the row is what makes that unreachable.
    """
    from mishne.db import transcripts as writes

    with _session(owner) as s:
        writes.record_asset(s, ORG, _ingest(), asset_id=ASSET, ingest_version=7)
        writes.record_asset(s, ORG, _ingest(), asset_id=second_row,
                            ingest_version=7)
        s.commit()

    rows = _beats(owner)
    assert len(rows) == 4
    assert {r.asset_id for r in rows} == {ASSET, second_row}
    assert _d(0) in {r.id for r in rows}


@pytest.fixture
def second_row(owner):
    """A second upload of the same bytes: same checksum, its own row."""
    other = "ast_transcript_test_2"
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                "duration_frames, start_tc_frames, probe, probed_at) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'ready', 'rushes.mov', 1024, "
                ":c, 25, 1, 90000, :tc, '{}'::jsonb, now())"
            ),
            {"a": other, "o": ORG, "p": PROJECT, "c": "e" * 64, "tc": START_TC},
        )
    yield other
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM assets WHERE id = :a"), {"a": other})


def _beats(owner):
    with owner.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT id, asset_id, start_frames, end_frames, speaker "
                "FROM beats "
                "WHERE org_id = :o ORDER BY idx"
            ),
            {"o": ORG},
        ).all()
