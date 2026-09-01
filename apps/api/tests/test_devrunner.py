"""The loop that makes a submitted job actually run on one machine.

Without it, `POST /v1/jobs` writes `queued` and nothing ever reads it: the
browser shows a progress panel that never moves and the person concludes the
product is broken. In production Step Functions is the reader; locally this is.

It is also the reader for the step before a job: an upload waits in `probing`
for an S3 event notification, and MinIO on a laptop sends that notification
nowhere. The asset stayed `probing` for ever — no duration, so no price, so
filtered out of the source list — and the new-cut screen said there was nothing
to cut while the uploads sat in the bucket.

Three properties, and the last is the one that bites:

* it finds queued work across every tenant, which row-level security makes a
  question no tenant-scoped session can ask;
* it finds unprobed assets the same way, and calls the same `probe_asset` the
  notification would have;
* **a job that cannot even be started is failed, not retried — and refunded.**
  `worker.execute` handles a failure during a run and on the completion path
  after it, but a failure before either — media that is not where the row says
  it is — raises straight out of it. A job left `queued` is picked up again on
  the next poll, forever, failing the same way every two seconds. And the hold
  is placed by the API at submission, not by the worker, so marking the status
  without touching the ledger leaves the customer's credits held against a job
  that will never run.
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, requires_schema  # noqa: E402

pytestmark = requires_schema

JOB = "job_devrunner"
ASSET = "ast_devrunner"


@pytest.fixture(autouse=True)
def isolated_main(monkeypatch):
    """Stop `main()` reshaping the process it is called in.

    It is a command-line entry point and does two things that are right for a
    process it owns and wrong inside a test runner:

    * `load_env_file` reads `apps/api/.env` into `os.environ`, where it stays
      for every test that runs afterwards. On a developer's machine that file
      points DATABASE_URL, USE_MOCKS and S3_ENDPOINT_URL at their own setup, so
      one call here quietly re-pointed the rest of the suite — twelve failures
      in four unrelated files, and S3 calls hanging against a MinIO that is not
      running.
    * `signal.signal` replaces pytest's own SIGINT and SIGTERM handlers, which
      is how a hung run stops responding to the thing sent to kill it.

    Neither belongs to what these tests are about.
    """
    monkeypatch.setattr("mishne.orchestration.devrunner.load_env_file",
                        lambda *_a, **_k: [])
    monkeypatch.setattr("mishne.orchestration.devrunner.signal.signal",
                        lambda *_a, **_k: None)


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


def _queue_a_job(owner) -> str:
    """A queued job under an id nothing has used before.

    `purge_org` deliberately leaves `credit_ledger` alone — it is append-only at
    the database — so the two tests below cannot share a fixed job id with each
    other or with their own last run: leftover rows would make them pass without
    the code doing anything.
    """
    job = f"job_dr_{secrets.token_hex(4)}"
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO jobs (id, org_id, project_id, status, mode, "
                "notes_raw, brief, estimate, approved_cap) VALUES "
                "(:j, :o, :p, 'queued', 'ai', '', '{}', '{}', 10)"
            ),
            {"j": job, "o": ORG, "p": PROJECT},
        )
    return job


def _money(owner, job: str) -> tuple[list[tuple[str, float]], float, float]:
    """This job's ledger entries, and the org's projected balance."""
    with owner.begin() as conn:
        entries = [
            (r.kind, float(r.delta))
            for r in conn.execute(
                sa.text("SELECT kind, delta FROM credit_ledger WHERE job_id = :j"),
                {"j": job},
            )
        ]
        available, held = conn.execute(
            sa.text("SELECT available, held FROM org_balances WHERE org_id = :o"),
            {"o": ORG},
        ).one()
    return entries, float(available), float(held)


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
def test_a_job_that_cannot_start_gets_its_hold_back(owner, tenant, monkeypatch):
    """The half of `_fail` that was missing, and it is the half that costs money.

    The hold is placed by the API at submission, not by the worker, so a job
    that dies before `execute` reaches its own failure handling has already
    taken the customer's credits and there is nobody else to give them back.
    Setting the status alone leaves `failed` on the jobs page beside a balance
    that still shows the cap held, with nothing in the ledger to explain it.
    """
    from mishne.orchestration import devrunner

    job = _queue_a_job(owner)
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO credit_ledger (id, org_id, project_id, job_id, "
                "kind, delta, balance_after, description) VALUES "
                "(:l, :o, :p, :j, 'hold', -10, 490, 'job submitted')"
            ),
            {"l": f"led_{secrets.token_hex(8)}", "o": ORG, "p": PROJECT, "j": job},
        )
        conn.execute(
            sa.text("UPDATE org_balances SET available = 490, held = 10 "
                    "WHERE org_id = :o"),
            {"o": ORG},
        )

    monkeypatch.setattr(devrunner, "execute",
                        lambda *a, **k: (_ for _ in ()).throw(
                            FileNotFoundError("not where the row says")))
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    with owner.begin() as conn:
        status = conn.execute(
            sa.text("SELECT status FROM jobs WHERE id = :j"), {"j": job}
        ).scalar_one()
    entries, available, held = _money(owner, job)
    assert status == "failed"
    assert ("release", 10.0) in entries
    # The projection moved with the row, which is the invariant the ledger is for.
    assert (available, held) == (500.0, 0.0)


@requires_schema
def test_a_job_with_no_hold_is_failed_without_inventing_a_refund(
    owner, tenant, monkeypatch
):
    """`_fail` knows a job ended badly, not what its ledger says.

    So the guard is in `db.jobs.release`: releasing a job that was never held
    would credit an account for money it never paid, which is the same class of
    error as the stranded hold and harder to notice, because the balance goes up.
    """
    from mishne.orchestration import devrunner

    job = _queue_a_job(owner)
    monkeypatch.setattr(devrunner, "execute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    with owner.begin() as conn:
        status = conn.execute(
            sa.text("SELECT status FROM jobs WHERE id = :j"), {"j": job}
        ).scalar_one()
    entries, available, held = _money(owner, job)
    assert status == "failed"
    assert entries == []
    assert (available, held) == (500.0, 0.0)


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


@pytest.fixture
def unprobed_asset(tenant, owner):
    """An upload that has completed and is waiting for stage 0.

    Written directly, in the state `complete_upload` leaves behind: status
    `probing`, no `upload_id`, a placeholder rate of 1/1 and no duration.
    """
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, s3_bucket, s3_key, "
                "edit_rate_num, edit_rate_den, duration_frames) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'probing', 'A001.mxf', 1, "
                ":c, 'test-raw', :k, 1, 1, 0)"
            ),
            {"a": ASSET, "o": ORG, "p": PROJECT, "c": "0" * 64,
             "k": f"orgs/{ORG}/projects/{PROJECT}/assets/{ASSET}/source"},
        )
    yield ASSET
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM assets WHERE org_id = :o"), {"o": ORG})


@requires_schema
def test_unprobed_assets_are_found_across_tenants(owner, unprobed_asset):
    """The second privileged read, and the same two id columns as the first."""
    from mishne.orchestration import devrunner

    assert (ORG, ASSET) in devrunner._unprobed(owner, limit=50)


@requires_schema
def test_an_asset_that_has_been_probed_is_left_alone(owner, unprobed_asset):
    """Otherwise every completed upload is re-read on every poll, for ever."""
    from mishne.orchestration import devrunner

    with owner.begin() as conn:
        conn.execute(sa.text("UPDATE assets SET status = 'ready' WHERE id = :a"),
                     {"a": ASSET})

    assert all(a != ASSET for _org, a in devrunner._unprobed(owner, limit=50))


@requires_schema
def test_the_loop_probes_a_completed_upload(owner, unprobed_asset, monkeypatch):
    """The bug this closes: nothing on one machine called stage 0 at all, so an
    upload was `probing` for ever and the new-cut screen had no source to offer."""
    from mishne.orchestration import devrunner

    probed: list[tuple[str, str]] = []

    def record(org_id, asset_id, settings=None):
        probed.append((org_id, asset_id))
        with owner.begin() as conn:
            conn.execute(sa.text("UPDATE assets SET status = 'ready' WHERE id = :a"),
                         {"a": asset_id})
        return "ready"

    monkeypatch.setattr(devrunner, "probe_asset", record)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    assert (ORG, ASSET) in probed


@requires_schema
def test_a_probe_that_raises_leaves_the_row_saying_probing(owner, unprobed_asset,
                                                           monkeypatch):
    """`probe_asset` marks the row itself, failure included. If it raises before
    getting that far, the loop must not pretend the asset has been read — and
    must not take the whole runner down with it either."""
    from mishne.orchestration import devrunner

    calls: list[str] = []

    def explode(org_id, asset_id, settings=None):
        calls.append(asset_id)
        raise RuntimeError("the database went away")

    monkeypatch.setattr(devrunner, "probe_asset", explode)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    assert devrunner.main(["--once"]) == 0

    assert calls == [ASSET]
    with owner.begin() as conn:
        status = conn.execute(
            sa.text("SELECT status FROM assets WHERE id = :a"), {"a": ASSET}
        ).scalar_one()
    assert status == "probing"


@requires_schema
def test_a_repeated_failure_is_attempted_once_per_process(owner, unprobed_asset,
                                                          monkeypatch):
    """A row that stays `probing` after a call is selected again on the next
    poll. Without the `attempted` guard that is a hot loop on one asset: the
    same log line every two seconds, for ever."""
    from mishne.orchestration import devrunner

    calls: list[str] = []

    def explode(org_id, asset_id, settings=None):
        calls.append(asset_id)
        raise RuntimeError("the database went away")

    monkeypatch.setattr(devrunner, "probe_asset", explode)

    # Two passes over the same still-`probing` row — which is what the real
    # poll would return — and then out, rather than looping for ever.
    passes: list[int] = []

    def poll(engine, limit=1):
        if len(passes) >= 2:
            raise KeyboardInterrupt
        passes.append(1)
        return [(ORG, ASSET)]

    monkeypatch.setattr(devrunner, "_unprobed", poll)
    monkeypatch.setattr(devrunner.time, "sleep", lambda _s: None)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    with pytest.raises(KeyboardInterrupt):
        devrunner.main([])

    # Two polls both offered the asset; only the first pass tried it.
    assert len(passes) == 2
    assert calls == [ASSET]


@requires_schema
def test_once_drains_every_waiting_upload(owner, unprobed_asset, monkeypatch):
    """`--once` polls one asset at a time, so stopping after the first would
    leave a second upload `probing` until the next run."""
    from mishne.orchestration import devrunner

    second = "ast_devrunner_2"
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, bytes, checksum, s3_bucket, s3_key, "
                "edit_rate_num, edit_rate_den, duration_frames) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'probing', 'A002.mxf', 1, "
                ":c, 'test-raw', :k, 1, 1, 0)"
            ),
            {"a": second, "o": ORG, "p": PROJECT, "c": "1" * 64,
             "k": f"orgs/{ORG}/projects/{PROJECT}/assets/{second}/source"},
        )

    def record(org_id, asset_id, settings=None):
        with owner.begin() as conn:
            conn.execute(sa.text("UPDATE assets SET status = 'ready' WHERE id = :a"),
                         {"a": asset_id})
        return "ready"

    monkeypatch.setattr(devrunner, "probe_asset", record)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")

    devrunner.main(["--once"])

    assert devrunner._unprobed(owner, limit=50) == []
