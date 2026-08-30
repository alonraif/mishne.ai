"""Alerting: what pages, and — more importantly — what does not.

The definition of done is "a failed job pages somebody, and a job that merely
retried does not". The second half is the hard one and it is where most
alerting goes wrong: a monitor that fires on something the system already
handled trains its reader to close it without looking, and then the one that
mattered is closed without looking too.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne import alerts  # noqa: E402
from mishne.orchestration import graph  # noqa: E402
from mishne.orchestration.runner import RecordingSink, run_job  # noqa: E402
from mishne.pipeline.steps import STEP_NAMES  # noqa: E402


def _request(tmp_path: Path) -> graph.JobRequest:
    return graph.JobRequest(
        job_id="job_alert",
        org_id="org_1",
        project_id="prj_1",
        assets=[graph.AssetSource(asset_id="ast_1", path=tmp_path / "a.mov",
                                  content_id="ast_1")],
        out_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
    )


def test_a_page_and_a_notice_are_different_things():
    """The distinction is the whole value of the module, so it is data rather
    than a convention somebody remembers."""
    assert alerts.job_failed("job_1", step="transcribe", reason="TimeoutError",
                             attempts=3).severity == alerts.PAGE


def test_a_failed_job_alert_carries_no_message():
    """`reason` is an exception type. A provider's message can quote a
    filename, and an alert is read by more people than a log."""
    secret = "rushes_from_the_hospital_interview.mov"
    alert = alerts.job_failed("job_1", step="transcribe",
                              reason="FileNotFoundError", attempts=3)
    assert secret not in str(alert.facts)
    assert alert.facts["reason"] == "FileNotFoundError"


def test_a_step_that_retried_and_succeeded_raises_nothing(monkeypatch, tmp_path):
    """The runner retries the model stages because a provider returning 503 is
    not a reason to fail somebody's job. Paging on that is paging on the system
    working — so the failure alert is raised from the worker's terminal path,
    which a recovered step never reaches.
    """
    attempts = {"n": 0}

    def make(name):
        def _step(ctx, state):
            if name != "score":
                return ""
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise RuntimeError("provider had a moment")
            return ""
        return _step

    monkeypatch.setattr(
        graph, "IMPLEMENTATIONS", {name: make(name) for name in STEP_NAMES}
    )
    sink = RecordingSink()
    # It completes. Nothing raised out of the runner, so the worker's failure
    # path — the only place `job_failed` is emitted — is never entered.
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)
    assert attempts["n"] == 2
    assert all(s.status == "done" for s in sink.steps)


# ── the queries ───────────────────────────────────────────────────────────

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

JOB = "job_alert_rules"


@pytest.fixture
def job_row(tenant, owner):
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'queued', 'ai', '', '{}'::jsonb, '{}'::jsonb, 10)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT},
        )
    yield JOB
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def _history(conn, step: str, seconds: float, count: int, from_cache=False,
             prefix: str = "h"):
    """`count` previous runs of `step`, each taking `seconds`.

    `prefix` keeps two calls in one test from colliding on the job id — which
    they otherwise do, silently, as a primary key violation in a fixture.
    """
    for n in range(count):
        job = f"job_hist_{prefix}_{n:03d}"
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'complete', 'ai', '', '{}'::jsonb, '{}'::jsonb, 10)"
            ),
            {"j": job, "o": ORG, "p": PROJECT},
        )
        conn.execute(
            sa.text(
                "INSERT INTO job_steps (id, org_id, job_id, idx, name, status, "
                "seconds, from_cache) VALUES "
                "(:i, :o, :j, 1, :n, 'done', :s, :c)"
            ),
            {"i": f"stp_{job}_01", "o": ORG, "j": job, "n": step,
             "s": seconds, "c": from_cache},
        )


@requires_schema
def test_a_slow_step_needs_a_distribution_to_have_left(owner, job_row):
    """Below the sample floor there is nothing to compare against, and three
    samples produce a confident answer that means nothing."""
    from sqlalchemy.orm import Session

    with owner.begin() as conn:
        _history(conn, "transcribe", seconds=60.0, count=3)
    try:
        with Session(owner) as s:
            assert alerts.slow_step(s, ORG, JOB, step="transcribe",
                                    seconds=9000.0) is None
    finally:
        with owner.begin() as conn:
            conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


@requires_schema
def test_a_step_far_outside_its_own_distribution_is_a_notice(owner, job_row):
    from sqlalchemy.orm import Session

    with owner.begin() as conn:
        _history(conn, "transcribe", seconds=60.0, count=25)
    try:
        with Session(owner) as s:
            ordinary = alerts.slow_step(s, ORG, JOB, step="transcribe", seconds=90.0)
            wedged = alerts.slow_step(s, ORG, JOB, step="transcribe", seconds=900.0)
    finally:
        with owner.begin() as conn:
            conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})

    # A minute and a half against a one-minute median is longer material, not a
    # problem. Fifteen minutes is a stage that is not doing what it does.
    assert ordinary is None
    assert wedged is not None
    assert wedged.severity == alerts.NOTICE
    assert wedged.facts["median_seconds"] == 60.0


@requires_schema
def test_cache_hits_are_not_part_of_the_distribution(owner, job_row):
    """A cached stage took no time because it did no work. Averaging those into
    the median drags it toward zero and makes every real execution an outlier —
    which is an alert that fires on the system working, again."""
    from sqlalchemy.orm import Session

    with owner.begin() as conn:
        _history(conn, "transcribe", seconds=60.0, count=25, prefix="real")
        _history(conn, "transcribe", seconds=0.01, count=25, from_cache=True,
                 prefix="cached")
    try:
        with Session(owner) as s:
            assert alerts.slow_step(s, ORG, JOB, step="transcribe",
                                    seconds=90.0) is None
    finally:
        with owner.begin() as conn:
            conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})
