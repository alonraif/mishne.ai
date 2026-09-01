"""The runner: order, retries, cancellation, and the cache that makes a re-run cheap.

Driven with a stubbed pipeline rather than real media. What is under test is the
orchestration — that steps run in the registry's order, that a flaky stage is
retried and a wrong-artifact stage is not, that a cancel between steps stops the
job — and real footage would only make those assertions slower and less exact.
`test_reference_run.py` is where the real pipeline is checked, against a sample.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.orchestration import graph  # noqa: E402
from mishne.orchestration.runner import (  # noqa: E402
    Cancelled,
    RecordingSink,
    plan,
    run_job,
)
from mishne.pipeline.steps import ASSET_STEPS, JOB_STEPS, STEP_NAMES  # noqa: E402


@pytest.fixture
def stub(monkeypatch):
    """Every step replaced by one that records that it ran."""
    ran: list[str] = []

    def make(name):
        def _step(ctx, state):
            ran.append(f"{name}:{state.current}" if state.current and name in
                       {s.name for s in ASSET_STEPS} else name)
            return f"{name} ok"
        return _step

    monkeypatch.setattr(
        graph, "IMPLEMENTATIONS", {name: make(name) for name in STEP_NAMES}
    )
    return ran


def _request(tmp_path: Path, assets=("ast_1",)) -> graph.JobRequest:
    return graph.JobRequest(
        job_id="job_1",
        org_id="org_1",
        project_id="prj_1",
        assets=[
            graph.AssetSource(asset_id=a, path=tmp_path / f"{a}.mov", content_id=a)
            for a in assets
        ],
        out_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
    )


def test_the_steps_run_in_the_registrys_order(stub, tmp_path):
    sink = RecordingSink()
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)

    expected = [f"{s.name}:ast_1" for s in ASSET_STEPS] + [s.name for s in JOB_STEPS]
    assert stub == expected


def test_the_asset_phase_runs_once_per_upload(stub, tmp_path):
    run_job(_request(tmp_path, ("ast_1", "ast_2")), sleep=lambda _s: None)

    for spec in ASSET_STEPS:
        assert stub.count(f"{spec.name}:ast_1") == 1
        assert stub.count(f"{spec.name}:ast_2") == 1
    for spec in JOB_STEPS:
        assert stub.count(spec.name) == 1


def test_the_job_status_walks_forward_as_the_stages_do(stub, tmp_path):
    sink = RecordingSink()
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)
    assert sink.statuses == [
        "preparing", "transcribing", "analyzing", "selecting", "assembling", "validating",
    ]


def test_a_flaky_stage_is_retried_and_the_job_survives(monkeypatch, tmp_path):
    attempts = {"transcribe": 0}
    impls = {name: (lambda ctx, state: "ok") for name in STEP_NAMES}

    def flaky(ctx, state):
        attempts["transcribe"] += 1
        if attempts["transcribe"] < 3:
            raise RuntimeError("the ASR provider returned 503")
        return "407 words"

    impls["transcribe"] = flaky
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    sink = RecordingSink()
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)

    # A provider having a bad minute is not a reason to fail somebody's job.
    assert attempts["transcribe"] == 3
    transcribe = [s for s in sink.steps if s.name == "transcribe"][-1]
    assert transcribe.status == "done"
    assert transcribe.attempt == 3


def test_a_stage_that_is_wrong_rather_than_unlucky_is_not_retried(monkeypatch, tmp_path):
    attempts = {"validate": 0}
    impls = {name: (lambda ctx, state: "ok") for name in STEP_NAMES}

    def broken(ctx, state):
        attempts["validate"] += 1
        raise ValueError("AAF failed round-trip validation")

    impls["validate"] = broken
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    with pytest.raises(ValueError):
        run_job(_request(tmp_path), sleep=lambda _s: None)

    # Writing the same wrong artifact again produces the same wrong artifact,
    # and charges for it.
    assert attempts["validate"] == 1


def test_a_stage_that_never_recovers_gives_up_and_raises(monkeypatch, tmp_path):
    attempts = {"n": 0}
    impls = {name: (lambda ctx, state: "ok") for name in STEP_NAMES}

    def always_fails(ctx, state):
        attempts["n"] += 1
        raise RuntimeError("no")

    impls["transcribe"] = always_fails
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    with pytest.raises(RuntimeError):
        run_job(_request(tmp_path), sleep=lambda _s: None)

    from mishne.pipeline.steps import STEPS_BY_NAME

    assert attempts["n"] == STEPS_BY_NAME["transcribe"].retries + 1


def test_cancelling_stops_the_job_between_steps(stub, tmp_path):
    sink = RecordingSink()
    sink.cancel_after = 3

    with pytest.raises(Cancelled):
        run_job(_request(tmp_path), sink, sleep=lambda _s: None)

    # Three steps ran and the fourth never started. No stage is killed part-way
    # through: that is how a half-written artifact reaches a customer.
    assert len(stub) == 3
    assert all(s.status == "done" for s in sink.steps)


def test_progress_detail_reaches_the_sink(stub, tmp_path):
    sink = RecordingSink()
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)
    assert all(s.detail for s in sink.steps)
    assert [s.label for s in sink.steps][0] == "Probe and normalize"


def test_a_cached_asset_skips_the_expensive_stages(monkeypatch, tmp_path):
    """Re-running a job with an unchanged asset performs zero transcription.

    That is the economics of the whole multi-upload feature: adding a fourth
    reel next month re-uses the three already transcribed (ADR-0008).
    """
    called: list[str] = []
    impls = dict(graph.IMPLEMENTATIONS)

    def transcribe(ctx, state):
        called.append("transcribe")
        return graph.step_transcribe(ctx, state)

    impls["transcribe"] = transcribe
    for name in ("brief", "propose", "score", "select", "refine", "assemble",
                 "emit", "validate", "transcript_page"):
        impls[name] = lambda ctx, state: "skipped"
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    request = _request(tmp_path)
    sentinel = object()

    class FakeIngest:
        asset_id = "ast_1"
        beats: list = []
        language = "en"
        duration_s = 1.0

    monkeypatch.setattr(
        graph.project, "cached_ingest", lambda adir, path, on_progress=None: FakeIngest()
    )

    sink = RecordingSink()
    run_job(request, sink, sleep=lambda _s: None)

    assert called == ["transcribe"]  # it ran…
    transcribe_step = [s for s in sink.steps if s.name == "transcribe"][0]
    assert transcribe_step.detail == "cached"  # …and did nothing
    assert sentinel is not None


def test_the_plan_is_the_rows_the_ui_needs_before_anything_runs(tmp_path):
    rows = plan(_request(tmp_path, ("ast_1", "ast_2")))
    assert len(rows) == len(ASSET_STEPS) * 2 + len(JOB_STEPS)
    assert [r[0] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0][1] == ASSET_STEPS[0].name
    assert rows[-1][1] == JOB_STEPS[-1].name
    assert rows[-1][2] == ""


def test_the_job_caches_land_under_a_workspace_as_well_as_a_path(tmp_path):
    """A worker hands the runner a `Workspace`, not a `Path`.

    `worker.prepare_request` sets `work_dir=workspace`, and every test above
    sets it to a `tmp_path` — so `RunState.job_dir` dividing it by a string was
    only ever exercised on the shape that supports it. On a real job that is a
    `TypeError` at stage 5, after every per-asset stage has already been paid
    for.
    """
    from mishne.workspace import S3Workspace

    workspace = S3Workspace(
        org_id="org_1", project_id="prj_1", scratch=tmp_path / "ws",
    )
    for work_dir, root in (
        (workspace, tmp_path / "ws"),
        (tmp_path / "work", tmp_path / "work"),
    ):
        request = _request(tmp_path)
        request.work_dir = work_dir
        job_dir = graph.RunState(request=request).job_dir
        assert job_dir == root / "jobs" / "job_1"
        assert job_dir.is_dir()


# ── what a failure leaves behind ──────────────────────────────────────────
#
# job_ef028410 failed three times with `{"code": "ASRError"}` and nothing else:
# no status, no step, no attempt count. The message may not be persisted — it
# can quote a filename — but everything below can be, and without it a
# transient rate limit and a permanently malformed request are the same row.


def test_a_failed_step_records_the_vendors_status_alongside_the_type(
        monkeypatch, tmp_path):
    impls = {name: (lambda ctx, state: "ok") for name in STEP_NAMES}

    class Refused(RuntimeError):
        status = 429

    def refused(ctx, state):
        raise Refused("HTTP 429: quota exceeded for project 12345")

    impls["transcribe"] = refused
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    sink = RecordingSink()
    with pytest.raises(Refused):
        run_job(_request(tmp_path), sink, sleep=lambda _s: None)

    run = [s for s in sink.steps if s.name == "transcribe"][-1]
    assert run.error_code == "Refused"
    assert run.error_status == 429
    # The message stays in memory for the process that raised it and goes no
    # further — `sink._error_facts` is what reaches the database.
    from mishne.orchestration.sink import _error_facts
    assert _error_facts(run) == {"code": "Refused", "status": 429}


def test_a_failure_that_is_not_a_vendor_response_has_no_status():
    from mishne.orchestration.runner import StepRun
    from mishne.orchestration.sink import _error_facts

    run = StepRun(idx=3, name="transcribe", label="Transcribe",
                  error_code="ValueError")

    assert _error_facts(run) == {"code": "ValueError"}


def test_the_exception_says_which_stage_and_which_attempt_ran_out(
        monkeypatch, tmp_path):
    """Both were read by the worker's alert with `getattr` and never set."""
    impls = {name: (lambda ctx, state: "ok") for name in STEP_NAMES}

    def broken(ctx, state):
        raise RuntimeError("nope")

    impls["transcribe"] = broken
    monkeypatch.setattr(graph, "IMPLEMENTATIONS", impls)

    with pytest.raises(RuntimeError) as caught:
        run_job(_request(tmp_path), sleep=lambda _s: None)

    assert caught.value.step == "transcribe"
    assert caught.value.attempt == 3


def test_what_reaches_job_steps_is_facts_on_failure_and_nothing_on_success(
        monkeypatch, tmp_path):
    """A step that failed twice and then succeeded is a step that succeeded.

    `error={}` is how the sink says "clear it": `upsert_step` stores an empty
    dict as NULL, and `None` would leave the previous attempt's failure sitting
    on a row whose status now reads `done`.
    """
    import contextlib

    from mishne.orchestration import sink as sink_mod
    from mishne.orchestration.runner import StepRun

    wrote: list[dict] = []

    class Writes:
        @staticmethod
        def record_llm_calls(*a, **kw):
            return 0

        @staticmethod
        def upsert_step(*a, **kw):
            wrote.append(kw)

    monkeypatch.setattr(sink_mod, "job_writes", Writes)
    monkeypatch.setattr(sink_mod, "session_for_org",
                        lambda org_id: contextlib.nullcontext(None))
    sink = sink_mod.DatabaseSink("org_1", "job_1")
    sink._check_duration = lambda run: None

    run = StepRun(idx=3, name="transcribe", label="Transcribe")
    run.error_code, run.error_status = "ASRError", 429
    sink.step_failed(run, will_retry=True)
    sink.step_failed(run, will_retry=False)
    sink.step_finished(run)

    assert wrote[0]["error"] == {"code": "ASRError", "status": 429}
    assert wrote[1]["error"] == {"code": "ASRError", "status": 429}
    # Empty, not None: None means "leave the column alone".
    assert wrote[2]["error"] == {}
