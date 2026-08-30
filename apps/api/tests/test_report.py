"""The cost report, exercised against rows rather than trusted.

`report.py` is the read side of everything C3 records, and it is the first thing
both C1 and C3 tell a new session to run. It had never been executed: every
number it prints comes from a query written against a schema that was itself new
in the same commit.

What is asserted here is mostly that the awkward cases are handled, because the
straightforward ones are obvious:

* a job that made no model calls says so, rather than printing $0.00 — which
  would be true and would read as evidence that models are cheap
* an unpriced model is counted separately, not summed as free
* a failover is not reported as two failures
* cached steps are excluded from the per-source-hour baseline
* the baseline says there is nothing to measure rather than dividing by zero
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

pytestmark = requires_schema

JOB = "job_report_test"
ASSET = "ast_report_test"


@pytest.fixture
def priced_job(tenant, owner):
    """One job: two stages, one of them cached, and three model calls."""
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, edit_rate_num, edit_rate_den, "
                "duration_frames, probe, probed_at) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'ready', 'rushes.mov', 1024, "
                ":c, 25, 1, 90000, '{}'::jsonb, now())"
            ),
            {"a": ASSET, "o": ORG, "p": PROJECT, "c": "f" * 64},
        )
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap, cost_cents) VALUES "
                "(:j, :o, :p, 'complete', 'ai', '', '{}'::jsonb, '{}'::jsonb, 10, 0)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT},
        )
        # An hour of material transcribed in six minutes, and a cached stage.
        conn.execute(
            sa.text(
                "INSERT INTO job_steps (id, org_id, job_id, idx, name, status, "
                "asset_id, seconds, cumulative_seconds, from_cache) VALUES "
                "(:i, :o, :j, 2, 'transcribe', 'done', :a, 360, 360, false)"
            ),
            {"i": f"stp_{JOB}_02", "o": ORG, "j": JOB, "a": ASSET},
        )
        conn.execute(
            sa.text(
                "INSERT INTO job_steps (id, org_id, job_id, idx, name, status, "
                "asset_id, seconds, cumulative_seconds, from_cache) VALUES "
                "(:i, :o, :j, 3, 'vad', 'done', :a, 0.04, 0.04, true)"
            ),
            {"i": f"stp_{JOB}_03", "o": ORG, "j": JOB, "a": ASSET},
        )
        for n, (task, model, micros, priced, fell_back, ok) in enumerate([
            ("score", "acme-1", 310_000, True, "", True),
            ("brief", "acme-next", 0, False, "", True),
            ("propose", "acme-1", 40_000, True, "other/beta-2", True),
        ]):
            conn.execute(
                sa.text(
                    "INSERT INTO job_llm_calls (id, org_id, job_id, step_idx, "
                    "step_name, task, provider, model, ok, cost_micros, priced, "
                    "fell_back_from) VALUES "
                    "(:i, :o, :j, 7, 'score', :t, 'acme', :m, :ok, :c, :pr, :fb)"
                ),
                {"i": f"llm_{JOB}_{n}", "o": ORG, "j": JOB, "t": task, "m": model,
                 "ok": ok, "c": micros, "pr": priced, "fb": fell_back},
            )
    yield JOB
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM assets WHERE id = :a"), {"a": ASSET})


def _render(owner, job_id: str) -> str:
    from sqlalchemy.orm import Session

    from mishne import report

    with Session(owner) as s:
        return "\n".join(report.render(job_id, report.job_breakdown(s, ORG, job_id)))


@requires_schema
def test_the_breakdown_shows_time_cost_and_the_cache(owner, priced_job):
    out = _render(owner, JOB)

    assert "transcribe" in out and "360.0s" in out
    # The cache hit is labelled, not silently fast.
    assert "cached" in out
    assert "1 of 2 steps served from cache" in out
    # 310_000 + 40_000 micros = $0.35
    assert "$  0.3500" in out


@requires_schema
def test_an_unpriced_model_is_flagged_rather_than_summed_as_free(owner, priced_job):
    """`Model.cost_for` returns None when the catalog has no price and the
    router stores 0.0. Reporting that as a free call is how a cost model
    concludes the models are cheap."""
    out = _render(owner, JOB)
    assert "UNPRICED" in out


@requires_schema
def test_a_failover_is_not_reported_as_a_failure(owner, priced_job):
    """The router crosses vendors mid-job. That is one call that succeeded."""
    out = _render(owner, JOB)
    assert "failover" in out
    assert "failed" not in out


@requires_schema
def test_a_job_with_no_model_calls_says_so_rather_than_zero(owner, tenant):
    """The heuristic scorer produces a real cut and no spend. "$0.00" would be
    true and would read as evidence that models are cheap — which is exactly the
    wrong conclusion to hand C1."""
    from sqlalchemy.orm import Session

    from mishne import report

    with Session(owner) as s:
        s.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'complete', 'ai', '', '{}'::jsonb, '{}'::jsonb, 10)"
            ),
            {"j": "job_no_models", "o": ORG, "p": PROJECT},
        )
        s.commit()
        out = "\n".join(
            report.render(
                "job_no_models", report.job_breakdown(s, ORG, "job_no_models")
            )
        )

    assert "made no model calls" in out


@requires_schema
def test_the_baseline_excludes_cache_hits(owner, priced_job):
    """One hour of material, six minutes of machine time — and a cached stage
    that must not drag the ratio toward zero."""
    from sqlalchemy.orm import Session

    from mishne import report

    with Session(owner) as s:
        out = "\n".join(report.transcription_baseline(s, ORG))

    assert "1 executed runs" in out
    # 360s machine / 3600s source = 0.10
    assert "0.10 machine-hours per source hour" in out


@requires_schema
def test_the_baseline_admits_when_there_is_nothing_to_measure(owner, tenant):
    """Which is the honest answer today, and must not be a division by zero or
    a confident zero."""
    from sqlalchemy.orm import Session

    from mishne import report

    with Session(owner) as s:
        out = "\n".join(report.transcription_baseline(s, ORG))

    assert "nothing to measure" in out
    assert "stays a guess" in out
