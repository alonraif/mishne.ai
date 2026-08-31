"""The loop that makes a submitted job actually run on one machine.

Without it, `POST /v1/jobs` writes `queued` and nothing ever reads it: the
browser shows a progress panel that never moves and the person concludes the
product is broken. In production Step Functions is the reader; locally this is.

Two properties, and the second is the one that bites:

* it finds queued work across every tenant, which row-level security makes a
  question no tenant-scoped session can ask;
* **a job that cannot even be started is failed, not retried.** `worker.execute`
  handles a failure during a run and releases the hold, but a failure before
  that — media that is not where the row says it is — raises straight out of it.
  A job left `queued` is picked up again on the next poll, forever, failing the
  same way every two seconds and holding the customer's credits the whole time.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

pytestmark = requires_schema

JOB = "job_devrunner"


@pytest.fixture
def queued_job(tenant, owner):
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'queued', 'ai', '', :brief, :est, 10)"
            ),
            {"j": JOB, "o": ORG, "p": PROJECT,
             "brief": '{"target_duration_s": 300}',
             "est": ('{"mode": "ai", "source_duration_frames": 15000, '
                     '"source_hours": 0.17, "lines": [], "subtotal": 10, '
                     '"cap": 10, "balance_before": 500, "balance_after": 490, '
                     '"sufficient": true, "shortfall": 0}')},
        )
    yield JOB
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM jobs WHERE org_id = :o"), {"o": ORG})


def _status(owner) -> str:
    with owner.begin() as conn:
        return conn.execute(
            sa.text("SELECT status FROM jobs WHERE id = :j"), {"j": JOB}
        ).scalar_one()


@requires_schema
def test_queued_work_is_found_across_tenants(owner, queued_job):
    """Every table's policy fails closed on an unset tenant, so this is the one
    privileged read in the runner — and it is two id columns."""
    from mishne.orchestration import devrunner

    found = devrunner._queued(owner, limit=50)

    assert (ORG, JOB) in found


@requires_schema
def test_a_job_that_is_not_queued_is_left_alone(owner, queued_job):
    from mishne.orchestration import devrunner

    with owner.begin() as conn:
        conn.execute(sa.text("UPDATE jobs SET status = 'complete' WHERE id = :j"),
                     {"j": JOB})

    assert all(job != JOB for _org, job in devrunner._queued(owner, limit=50))


@requires_schema
def test_a_job_that_cannot_start_is_failed_rather_than_retried_forever(
    owner, queued_job, monkeypatch
):
    """The whole reason `_fail` exists. Without it this job is picked up again
    two seconds later, and again, holding credits and filling the log."""
    from mishne.orchestration import devrunner

    def explode(org_id, job_id, settings=None):
        raise FileNotFoundError("the media is not where the row says it is")

    monkeypatch.setattr(devrunner, "execute", explode)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    assert _status(owner) == "failed"
    # And it is off the queue, which is the property that matters.
    assert all(job != JOB for _org, job in devrunner._queued(owner, limit=50))


@requires_schema
def test_the_recorded_error_is_a_type_and_not_a_message(owner, queued_job,
                                                        monkeypatch):
    """A message can quote a filename, and a job row is read by more people
    than wrote it (docs/architecture/04-security.md)."""
    from mishne.orchestration import devrunner

    monkeypatch.setattr(devrunner, "execute", lambda *a, **k: 1 / 0)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    with owner.begin() as conn:
        error = conn.execute(
            sa.text("SELECT error FROM jobs WHERE id = :j"), {"j": JOB}
        ).scalar_one()
    assert error == {"code": "ZeroDivisionError"}
