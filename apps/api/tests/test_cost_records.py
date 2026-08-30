"""What a job cost: attributed to a stage, kept per model, and free of content.

Workstream C3. Before this, `jobs.cost_cents` had no writer in the entire
repository and the worker built a `Router`, let it spend money, and dropped the
ledger on the floor — so "what does a job cost us" was not a question the
system could answer about itself, and C1 cannot price a credit without it.

Three properties are under test here and each one failed silently before:

* **A step's cost is the calls it made.** The router's ledger is a flat list per
  job and knows nothing about stages, so cost per stage is a slicing problem at
  the step boundary, which is in the runner.
* **A retry costs what all of its attempts cost.** Both in seconds and in money.
* **None of it carries customer content.** A provider's error message is the
  likeliest leak, because it quotes what it was sent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mishne.llm.base import CallRecord, Ledger  # noqa: E402
from mishne.orchestration import graph  # noqa: E402
from mishne.orchestration.runner import RecordingSink, run_job  # noqa: E402
from mishne.pipeline.steps import ASSET_STEPS, STEP_NAMES  # noqa: E402


class FakeRouter:
    """A router that spends money on demand, with a ledger like the real one."""

    def __init__(self) -> None:
        self.ledger = Ledger()

    def spend(self, task: str, usd: float, *, ok: bool = True) -> None:
        self.ledger.add(
            CallRecord(task=task, provider="acme", model="acme-1", ok=ok,
                       latency_ms=120, input_tokens=1000, output_tokens=100,
                       cost_usd=usd)
        )


def _request(tmp_path: Path, router=None, assets=("ast_1",)) -> graph.JobRequest:
    return graph.JobRequest(
        job_id="job_cost",
        org_id="org_1",
        project_id="prj_1",
        assets=[
            graph.AssetSource(asset_id=a, path=tmp_path / f"{a}.mov", content_id=a)
            for a in assets
        ],
        out_dir=tmp_path / "out",
        work_dir=tmp_path / "work",
        router=router,
    )


# ── attribution ───────────────────────────────────────────────────────────


def test_a_model_call_is_attributed_to_the_step_that_made_it(monkeypatch, tmp_path):
    """The ledger is per job; the cost question is per stage."""
    router = FakeRouter()
    spenders = {"brief": 0.02, "score": 0.31}

    def make(name):
        def _step(ctx, state):
            if name in spenders:
                router.spend(name, spenders[name])
            return ""
        return _step

    monkeypatch.setattr(
        graph, "IMPLEMENTATIONS", {name: make(name) for name in STEP_NAMES}
    )
    sink = RecordingSink()
    run_job(_request(tmp_path, router), sink, sleep=lambda _s: None)

    by_step = {s.name: s.llm_calls for s in sink.steps}
    assert [c.cost_usd for c in by_step["brief"]] == [0.02]
    assert [c.cost_usd for c in by_step["score"]] == [0.31]
    # Every other stage spent nothing, and says so rather than being unrecorded.
    assert all(not by_step[n] for n in by_step if n not in spenders)


def test_a_retry_is_charged_for_every_attempt(monkeypatch, tmp_path):
    """A step that failed spent money before it failed. Recording only the
    attempt that succeeded is how a retry storm stays invisible in the cost
    model.

    One failure, not two: `score` is declared with `retries=1`, so the runner
    allows exactly two attempts. A test that fails twice is testing that the
    job dies, which `test_runner.py` already covers.
    """
    router = FakeRouter()
    attempts = {"n": 0}

    def make(name):
        def _step(ctx, state):
            if name != "score":
                return ""
            attempts["n"] += 1
            router.spend("score", 0.10, ok=attempts["n"] == 2)
            if attempts["n"] < 2:
                raise RuntimeError("provider had a moment")
            return ""
        return _step

    monkeypatch.setattr(
        graph, "IMPLEMENTATIONS", {name: make(name) for name in STEP_NAMES}
    )
    sink = RecordingSink()
    run_job(_request(tmp_path, router), sink, sleep=lambda _s: None)

    score = next(s for s in sink.steps if s.name == "score")
    assert len(score.llm_calls) == 2
    assert round(sum(c.cost_usd for c in score.llm_calls), 2) == 0.20
    # And the time: cumulative covers the failed attempts, `seconds` does not.
    assert score.cumulative_seconds >= score.seconds


def test_a_cached_asset_phase_says_so(monkeypatch, tmp_path):
    """A stage that took no time because it did no work has to be
    distinguishable from a stage that was fast. Otherwise a cost baseline
    averages the cache hits into the price of the work they skipped."""
    monkeypatch.setattr(
        graph,
        "IMPLEMENTATIONS",
        {name: (lambda ctx, state: "") for name in STEP_NAMES},
    )
    monkeypatch.setattr(
        "mishne.pipeline.project.cached_ingest", lambda adir, path: object()
    )
    sink = RecordingSink()
    run_job(_request(tmp_path), sink, sleep=lambda _s: None)

    asset_step_names = {s.name for s in ASSET_STEPS}
    asset_steps = [s for s in sink.steps if s.name in asset_step_names]
    assert asset_steps and all(s.from_cache for s in asset_steps)
    assert not any(s.from_cache for s in sink.steps if s.name not in asset_step_names)


# ── the content rule ──────────────────────────────────────────────────────


def test_a_failed_call_records_the_exception_type_not_its_message(monkeypatch):
    """A vendor quotes the prompt back in its error string often enough that the
    message is customer content — and this record is written into the job's
    manifest and, now, into the database. See docs/architecture/04-security.md.
    """
    from mishne.llm import router as router_module
    from mishne.llm.base import LLMError

    secret = "the interview where she talks about the lawsuit"

    class Boom:
        name = "google"

        def complete(self, **kw):
            raise LLMError(f"400: bad request for prompt {secret!r}")

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(router_module.providers, "get", lambda name: Boom())

    r = router_module.Router()
    assert r.plan("score"), "the test needs at least one candidate model"
    with pytest.raises(LLMError):
        r.complete("score", system="s", user=secret)

    assert r.ledger.calls, "a failed call is still a call and must be recorded"
    for call in r.ledger.calls:
        blob = " ".join(str(v) for v in call.to_dict().values())
        assert secret not in blob
        assert "lawsuit" not in blob
        assert call.error == "LLMError"


def test_an_unpriced_model_is_not_a_free_model():
    """`Model.cost_for` returns None when the catalog has no price, and the
    router stores `cost_usd=cost or 0.0`. Without `priced`, that zero is
    indistinguishable from a call that genuinely cost nothing, and a billing
    path that sums them under-charges silently."""
    unknown = CallRecord(task="score", provider="acme", model="acme-next",
                         ok=True, cost_usd=0.0, priced=False)
    free = CallRecord(task="score", provider="acme", model="acme-free",
                      ok=True, cost_usd=0.0, priced=True)

    assert unknown.priced is not free.priced
    # And it survives into the manifest: `v not in ("", 0, 0.0)` dropped every
    # False, because False == 0 in Python — which silently removed the two
    # fields whose False is the reason they exist.
    assert unknown.to_dict()["priced"] is False
    assert CallRecord(task="t", provider="p", model="m", ok=False).to_dict()["ok"] is False


# ── persistence ───────────────────────────────────────────────────────────

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

JOB = "job_cost_records"


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


@requires_schema
def test_the_job_row_is_a_projection_of_the_call_rows(owner, job_row):
    from sqlalchemy.orm import Session

    from mishne.db import jobs as job_writes

    calls = [
        CallRecord(task="brief", provider="acme", model="acme-1", ok=True,
                   cost_usd=0.021),
        CallRecord(task="score", provider="acme", model="acme-1", ok=True,
                   cost_usd=0.310),
    ]
    with Session(owner) as s:
        spend = job_writes.record_llm_calls(s, ORG, JOB, 7, "score", calls)
        # Micros, not cents: 0.021 is two cents and a bit, and rounding each
        # call to cents before summing is how model spend reads as zero.
        assert spend == 331_000
        cents = job_writes.set_job_cost(
            s, ORG, JOB, model_versions={"score": ["acme/acme-1"]}
        )
        s.commit()

    assert cents == 33
    with owner.begin() as conn:
        row = conn.execute(
            sa.text("SELECT cost_cents, model_versions FROM jobs WHERE id = :j"),
            {"j": JOB},
        ).one()
    assert row.cost_cents == 33
    assert row.model_versions == {"score": ["acme/acme-1"]}


@requires_schema
def test_re_running_a_step_rewrites_its_calls_rather_than_doubling_them(owner, job_row):
    """Resume is idempotent re-execution (ADR-0016), so a step runs again after
    a worker dies. A cost record that appended would double the job's spend
    every time the machine it ran on was replaced."""
    from sqlalchemy.orm import Session

    from mishne.db import jobs as job_writes

    calls = [CallRecord(task="score", provider="acme", model="acme-1", ok=True,
                        cost_usd=0.25)]
    with Session(owner) as s:
        job_writes.record_llm_calls(s, ORG, JOB, 7, "score", calls)
        job_writes.record_llm_calls(s, ORG, JOB, 7, "score", calls)
        cents = job_writes.set_job_cost(s, ORG, JOB, model_versions={})
        s.commit()

    assert cents == 25
    with owner.begin() as conn:
        count = conn.execute(
            sa.text("SELECT count(*) FROM job_llm_calls WHERE job_id = :j"),
            {"j": JOB},
        ).scalar_one()
    assert count == 1


@requires_schema
def test_a_step_row_keeps_its_asset_its_duration_and_its_cache_flag(owner, job_row):
    """The three columns a per-source-hour transcription baseline is computed
    from, and none of them existed before 0005."""
    from sqlalchemy.orm import Session

    from mishne.db import jobs as job_writes

    with Session(owner) as s:
        job_writes.upsert_step(
            s, ORG, JOB, 2, "transcribe", status="active", started=True,
            asset_id="ast_a",
        )
        job_writes.upsert_step(
            s, ORG, JOB, 2, "transcribe", status="done", finished=True,
            asset_id="ast_a", seconds=41.5, cumulative_seconds=93.25,
            from_cache=True, model_cost_micros=0,
        )
        s.commit()

    with owner.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT asset_id, seconds, cumulative_seconds, from_cache "
                "FROM job_steps WHERE job_id = :j AND idx = 2"
            ),
            {"j": JOB},
        ).one()
    assert row.asset_id == "ast_a"
    assert row.seconds == pytest.approx(41.5)
    # The retries, which `finished_at - started_at` cannot see: this row was
    # overwritten by the attempt that succeeded.
    assert row.cumulative_seconds == pytest.approx(93.25)
    assert row.from_cache is True
