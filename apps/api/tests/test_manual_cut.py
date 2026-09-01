"""Manual and hybrid: the job stops for a person, and resumes with their cut.

`manual` and `hybrid` have been in the schema, the API and the UI since B1, and
the orchestrator ignored `mode` completely — `run_job` ran every stage to an
artifact, so no job ever reached `awaiting_edit`, the cut editor never had
anything to open, and `POST /jobs/{id}/cut` was a 501 for something that could
not have worked anyway.

What is under test:

* each mode runs the stages it should and stops where it should;
* a skipped stage does not shift the step numbering, because those numbers are
  the keys the progress rows were planned under;
* a submitted cut replaces the solver, in the person's order;
* a beat that is not this job's is refused rather than assembled against media
  the job never staged.

Driven with a stubbed pipeline, like `test_runner.py`: what is under test is
the orchestration, and real footage would make the assertions slower and less
exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.orchestration import graph  # noqa: E402
from mishne.orchestration.runner import (  # noqa: E402
    RecordingSink,
    phases_for,
    plan,
    run_job,
)
from mishne.pipeline.steps import ASSET_STEPS, STEP_NAMES  # noqa: E402


@pytest.fixture
def stub(monkeypatch):
    ran: list[str] = []

    def make(name):
        def _step(ctx, state):
            ran.append(name)
            return f"{name} ok"
        return _step

    monkeypatch.setattr(
        graph, "IMPLEMENTATIONS", {name: make(name) for name in STEP_NAMES}
    )
    return ran


def _request(tmp_path: Path, *, mode: str = "ai", user_cut=()) -> graph.JobRequest:
    return graph.JobRequest(
        job_id="job_1",
        org_id="org_1",
        project_id="prj_1",
        assets=[graph.AssetSource(asset_id="ast_1", path=tmp_path / "a.mov",
                                  content_id="ast_1")],
        out_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
        mode=mode,
        user_cut=list(user_cut),
    )


def _job_steps(ran: list[str]) -> list[str]:
    asset_names = {s.name for s in ASSET_STEPS}
    return [n for n in ran if n not in asset_names]


# ── which stages each mode runs ────────────────────────────────────────────


def test_an_ai_job_runs_everything_and_does_not_pause(stub, tmp_path):
    result = run_job(_request(tmp_path), sleep=lambda _s: None)
    assert result.paused is False
    assert "validate" in stub


def test_a_manual_job_stops_after_the_brief(stub, tmp_path):
    """Proposing and scoring candidates feeds a solver that will never run in
    manual mode. Paying a model to rank material a person is about to rank
    themselves is the definition of spend with no product in it."""
    result = run_job(_request(tmp_path, mode="manual"), sleep=lambda _s: None)

    assert result.paused_after == "brief"
    assert _job_steps(stub) == ["brief"]
    assert "propose" not in stub and "score" not in stub


def test_a_manual_briefs_model_call_is_not_made_at_all(tmp_path):
    """The stage runs; the model call inside it does not.

    Stage 5 is not skipped in manual mode — stage 9 needs `handle_frames` and
    the transcript page prints the brief. But every reader of the brief's
    *judgment* is skipped, so the model call was output nobody reads. On a job
    submitted with no notes it was worse than waste: it invented a tone and a
    narrative shape, wrote them to the transcript page, and no model ever
    chose a span with them.
    """
    from mishne.orchestration.graph import _brief_has_a_reader

    assert _brief_has_a_reader(_request(tmp_path, mode="ai")) is True
    assert _brief_has_a_reader(_request(tmp_path, mode="hybrid")) is True
    assert _brief_has_a_reader(_request(tmp_path, mode="manual")) is False
    # Resumed with a person's cut: `select` runs, but on their beats. The
    # solver is the only reader of the brief in that stage, and it does not run.
    assert _brief_has_a_reader(
        _request(tmp_path, mode="manual", user_cut=["b1"])) is False
    assert _brief_has_a_reader(
        _request(tmp_path, mode="hybrid", user_cut=["b1"])) is False


def test_a_manual_brief_still_carries_what_stage_9_and_the_page_need(tmp_path):
    """Deterministic is not degraded here — it is complete.

    `handle_frames` and the target come from the request, not from a model, so
    a manual brief compiled without one is missing nothing any later stage
    reads. A router that would raise if called proves the call is not made.
    """
    class ExplodingRouter:
        def available_for(self, task):  # pragma: no cover - must not be called
            raise AssertionError("a manual job must not consult a model here")

    request = _request(tmp_path, mode="manual")
    request.notes = "keep it punchy, about six minutes"
    request.handle_frames = 12
    request.router = ExplodingRouter()

    state = graph.RunState(request=request)
    state.assets = [_FakeIngest()]
    detail = graph.step_brief(graph.StepContext(job_id="job_1", org_id="org_1",
                                                project_id="prj_1"), state)

    assert state.brief.handle_frames == 12
    assert state.brief.target_duration_s == 360      # read from the notes
    assert "360s" in detail


class _FakeIngest:
    """Just enough of an `AssetIngest` for the job phase to gather."""

    asset_id = "ast_1"
    language = "en"
    duration_s = 10.0
    rate = None
    start_tc_frames = 0
    duration_frames = 250
    speech = None
    speakers: list = []

    def __init__(self):
        from mishne.pipeline.steps.structure import Beat

        self.beats = [Beat(id="b1", idx=0, asset_id="ast_1", speaker="",
                           start_ms=0, end_ms=1000, text="x", flags=[])]


def test_a_hybrid_job_stops_with_a_refined_suggestion(stub, tmp_path):
    """Refined rather than raw: the suggestion an editor judges should be the
    cut they would actually get — silence-snapped, handled, frame-accurate —
    not the solver's intention before stage 9 touched it."""
    result = run_job(_request(tmp_path, mode="hybrid"), sleep=lambda _s: None)

    assert result.paused_after == "refine"
    assert _job_steps(stub) == ["brief", "propose", "score", "select", "refine"]
    assert "assemble" not in stub


def test_a_resumed_job_skips_proposing_and_scoring(stub, tmp_path):
    """Both exist to feed the solver, and a person has replaced the solver."""
    result = run_job(
        _request(tmp_path, mode="hybrid", user_cut=["b1", "b2"]),
        sleep=lambda _s: None,
    )

    assert result.paused is False
    assert _job_steps(stub) == [
        "brief", "select", "refine", "assemble", "emit", "validate",
        "transcript_page",
    ]


def test_the_phase_plan_is_the_one_place_this_is_decided():
    assert phases_for("ai") == (frozenset(), "")
    assert phases_for("manual") == (frozenset({"propose", "score", "select"}), "brief")
    assert phases_for("hybrid") == (frozenset(), "refine")
    # A cut in hand overrides the mode's own pause: the person has already
    # edited, and stopping to ask them again is a job that never finishes.
    assert phases_for("hybrid", ["b1"]) == (frozenset({"propose", "score"}), "")


# ── the numbering that the progress panel depends on ───────────────────────


def test_a_skipped_stage_does_not_renumber_the_ones_after_it(stub, tmp_path):
    """`job_steps` rows are keyed on (job_id, idx) and were planned when the
    job was accepted. Renumbering around a skip writes each result onto the row
    of the step before it, and every stage in the panel is one place out — with
    every row still present and plausible."""
    request = _request(tmp_path, mode="hybrid", user_cut=["b1"])
    planned = {name: idx for idx, name, _asset in plan(request)}

    sink = RecordingSink()
    run_job(request, sink, sleep=lambda _s: None)

    for run in sink.steps:
        assert run.idx == planned[run.name], (
            f"{run.name} ran as step {run.idx}, planned as {planned[run.name]}"
        )


# ── the user's cut replaces the solver ─────────────────────────────────────


class FakeBeat:
    def __init__(self, beat_id: str, asset_id: str = "ast_1"):
        self.id = beat_id
        self.asset_id = asset_id
        self.duration_ms = 4_000


def _state(request, beats, candidates=()):
    state = graph.RunState(request=request)
    state.beats = beats
    state.candidates = list(candidates)
    state.scores = {}
    return state


def test_the_cut_is_taken_in_the_order_the_person_gave(tmp_path):
    request = _request(tmp_path, mode="manual", user_cut=["b3", "b1"])
    state = _state(request, [FakeBeat("b1"), FakeBeat("b2"), FakeBeat("b3")])

    detail = graph.step_select(None, state)

    assert [p.beat.id for p in state.picks] == ["b3", "b1"]
    assert [p.order_idx for p in state.picks] == [0, 1]
    assert "user cut" in detail


def test_a_carved_span_may_be_kept_by_name(tmp_path):
    """A hybrid editor keeps a span the proposer carved out of a long answer,
    and that span is a candidate rather than a beat."""
    request = _request(tmp_path, mode="hybrid", user_cut=["b2#a"])
    state = _state(request, [FakeBeat("b2")], candidates=[FakeBeat("b2#a")])

    graph.step_select(None, state)

    assert [p.beat.id for p in state.picks] == ["b2#a"]


def test_a_beat_the_job_does_not_have_is_an_error_not_a_silent_drop(tmp_path):
    """Dropping it would deliver a cut missing a line somebody explicitly
    asked for, and nothing in the output would say so."""
    request = _request(tmp_path, mode="manual", user_cut=["b1", "b_from_another_job"])
    state = _state(request, [FakeBeat("b1")])

    with pytest.raises(KeyError, match="b_from_another_job"):
        graph.step_select(None, state)


# ── the endpoint ───────────────────────────────────────────────────────────
#
# Against Postgres and the real API. What the unit tests above cannot check is
# the part that protects the job from a cut that is not its own.

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

CUT_ASSET = "ast_for_manual_cut"
CUT_JOB = "job_awaiting_an_edit"
OTHER_ASSET = "ast_someone_elses"


@pytest.fixture
def awaiting_edit(tenant, owner):
    """A manual job stopped for a person, with beats to choose between."""
    with owner.begin() as conn:
        for asset_id in (CUT_ASSET, OTHER_ASSET):
            conn.execute(
                sa.text(
                    "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                    "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                    "duration_frames, probe, probed_at) VALUES "
                    "(:a, :o, :p, 'video', 'full_media', 'ready', :f, 1024, "
                    ":c, 25, 1, 15000, '{}'::jsonb, now())"
                ),
                {"a": asset_id, "o": ORG, "p": PROJECT, "f": f"{asset_id}.mov",
                 "c": asset_id.ljust(64, "0")},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO transcripts (id, org_id, asset_id, provider, "
                    "provider_model, language) VALUES (:t, :o, :a, 'xai', "
                    "'grok-stt', 'en')"
                ),
                {"t": f"trs_{asset_id}", "o": ORG, "a": asset_id},
            )
            for idx in range(3):
                conn.execute(
                    sa.text(
                        "INSERT INTO beats (id, org_id, transcript_id, asset_id, "
                        "idx, start_frames, end_frames, speaker, text) VALUES "
                        "(:id, :o, :t, :a, :i, :s, :e, 'T1', 'a line')"
                    ),
                    {"id": f"{asset_id}_beat_{idx}", "o": ORG,
                     "t": f"trs_{asset_id}", "a": asset_id, "i": idx,
                     "s": 1000 + idx * 200, "e": 1100 + idx * 200},
                )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'awaiting_edit', 'manual', '', :brief, :est, 10)"
            ),
            {"j": CUT_JOB, "o": ORG, "p": PROJECT,
             "brief": '{"target_duration_s": 300}',
             "est": ('{"mode": "manual", "source_duration_frames": 15000, '
                     '"source_hours": 0.17, "lines": [], "subtotal": 10, '
                     '"cap": 10, "balance_before": 500, "balance_after": 490, '
                     '"sufficient": true, "shortfall": 0}')},
        )
        conn.execute(
            sa.text(
                "INSERT INTO job_assets (org_id, job_id, asset_id, order_idx) "
                "VALUES (:o, :j, :a, 0)"
            ),
            {"o": ORG, "j": CUT_JOB, "a": CUT_ASSET},
        )
    yield
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def _cut(owner) -> list[tuple[str, int]]:
    with owner.begin() as conn:
        return [
            (r.beat_id, r.order_idx)
            for r in conn.execute(
                sa.text(
                    "SELECT beat_id, order_idx FROM selections WHERE job_id = :j "
                    "ORDER BY order_idx"
                ),
                {"j": CUT_JOB},
            )
        ]


@requires_schema
def test_a_submitted_cut_is_stored_in_order_and_queues_the_job(
    api, owner, awaiting_edit
):
    http, _ = api

    resp = http.post(
        f"/v1/jobs/{CUT_JOB}/cut",
        json={"beat_ids": [f"{CUT_ASSET}_beat_2", f"{CUT_ASSET}_beat_0"]},
    )

    assert resp.status_code == 200, resp.text
    # Queued, not complete: the cut still has to go through stages 9-12, which
    # is what turns a list of beats into an AAF.
    assert resp.json()["status"] == "queued"
    assert _cut(owner) == [
        (f"{CUT_ASSET}_beat_2", 0),
        (f"{CUT_ASSET}_beat_0", 1),
    ]


@requires_schema
def test_saving_again_replaces_the_cut_rather_than_adding_to_it(
    api, owner, awaiting_edit
):
    """A person who removes a line and saves means the line is gone."""
    http, _ = api
    http.post(f"/v1/jobs/{CUT_JOB}/cut",
              json={"beat_ids": [f"{CUT_ASSET}_beat_0", f"{CUT_ASSET}_beat_1"]})
    with owner.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET status = 'awaiting_edit' WHERE id = :j"),
                     {"j": CUT_JOB})

    http.post(f"/v1/jobs/{CUT_JOB}/cut", json={"beat_ids": [f"{CUT_ASSET}_beat_0"]})

    assert _cut(owner) == [(f"{CUT_ASSET}_beat_0", 0)]


@requires_schema
def test_a_beat_from_another_upload_is_refused(api, owner, awaiting_edit):
    """It would assemble against media this job never staged — a timeline that
    looks entirely reasonable and points at frames of a different file."""
    http, _ = api

    resp = http.post(
        f"/v1/jobs/{CUT_JOB}/cut",
        json={"beat_ids": [f"{CUT_ASSET}_beat_0", f"{OTHER_ASSET}_beat_0"]},
    )

    assert resp.status_code == 422
    assert OTHER_ASSET in resp.json()["detail"]
    assert _cut(owner) == []


@requires_schema
def test_a_beat_that_does_not_exist_is_refused(api, awaiting_edit):
    http, _ = api
    resp = http.post(f"/v1/jobs/{CUT_JOB}/cut", json={"beat_ids": ["beat_nope"]})
    assert resp.status_code == 422


@requires_schema
def test_an_empty_cut_is_refused(api, awaiting_edit):
    http, _ = api
    assert http.post(f"/v1/jobs/{CUT_JOB}/cut", json={"beat_ids": []}).status_code == 422


@requires_schema
def test_a_job_that_is_not_waiting_for_an_edit_is_a_conflict(
    api, owner, awaiting_edit
):
    """Accepting a cut for a running job races the worker for the same rows."""
    http, _ = api
    with owner.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET status = 'assembling' WHERE id = :j"),
                     {"j": CUT_JOB})

    resp = http.post(f"/v1/jobs/{CUT_JOB}/cut",
                     json={"beat_ids": [f"{CUT_ASSET}_beat_0"]})

    assert resp.status_code == 409
    assert "assembling" in resp.json()["detail"]


@requires_schema
def test_an_ai_job_does_not_take_a_cut(api, owner, awaiting_edit):
    http, _ = api
    with owner.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET mode = 'ai' WHERE id = :j"),
                     {"j": CUT_JOB})

    resp = http.post(f"/v1/jobs/{CUT_JOB}/cut",
                     json={"beat_ids": [f"{CUT_ASSET}_beat_0"]})

    assert resp.status_code == 409


@requires_schema
def test_submitting_a_cut_changes_no_money(api, owner, awaiting_edit):
    """The hold placed at submission stands. A transcript the customer is still
    deciding about is not a deliverable, and settling here would charge for one."""
    with owner.begin() as conn:
        before = conn.execute(
            sa.text("SELECT available, held FROM org_balances WHERE org_id = :o"),
            {"o": ORG},
        ).one()
    http, _ = api

    http.post(f"/v1/jobs/{CUT_JOB}/cut",
              json={"beat_ids": [f"{CUT_ASSET}_beat_0"]})

    with owner.begin() as conn:
        after = conn.execute(
            sa.text("SELECT available, held FROM org_balances WHERE org_id = :o"),
            {"o": ORG},
        ).one()
        entries = conn.execute(
            sa.text("SELECT count(*) FROM credit_ledger WHERE job_id = :j"),
            {"j": CUT_JOB},
        ).scalar()
    assert (after.available, after.held) == (before.available, before.held)
    assert entries == 0
