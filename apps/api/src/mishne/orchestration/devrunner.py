"""Run queued jobs on a developer's machine. Local only.

    python -m mishne.orchestration.devrunner

## Why this exists and why it is not the worker

`worker.py` takes one job id and runs it. That is the right shape for
production, where Step Functions decides what runs and when: the machine is the
scheduler and the worker is a task it invokes (ADR-0002).

Nothing invokes it locally. A job submitted in the browser is written `queued`
and sits there, under a progress panel that never moves, while the person who
submitted it concludes the product is broken. This is the missing half of the
loop on one machine — a poll, and the same `execute` the real worker calls.

## Probing is the same gap, one step earlier

An upload lands in `probing` and waits for an S3 `ObjectCreated` notification
to call `mishne.probe`. MinIO on a laptop sends that notification nowhere, so
the asset stayed `probing` for ever: no duration, therefore no price, therefore
filtered out of the source list on the new-job screen. The uploads succeeded and
the product looked empty. So this polls for those too, oldest first, and calls
the same `probe_asset` the notification would have called.

## The one privileged read, and why it is bounded

Row-level security fails closed on an unset `app.org_id`, so "find every queued
job" is a question no tenant-scoped session can ask. This uses the owner
connection for exactly one SELECT of two id columns — `(org_id, id)` of queued
jobs — and nothing else. Execution then goes through `worker.execute`, which
opens ordinary tenant-scoped sessions and is subject to every policy in the
database, exactly as it is in production.

That is a deliberate, narrow exception and it is why this refuses to run
outside `environment=local`. In staging or production the answer is Step
Functions for jobs and a bucket notification for probes, not a loop with a
superuser connection in it. The privileged read for assets is the same shape:
`(org_id, id)` of assets in `probing`, and nothing else.

## One at a time

A worker holds the whole asset on local disk plus its derived audio (ADR-0013),
so two of them on a laptop is a disk problem before it is a speed one. Jobs run
oldest first, one at a time.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import sqlalchemy as sa

from ..config import get_settings, load_env_file
from ..db.base import normalise_url, session_for_org
from ..db import jobs as job_writes
from ..db import models as m
from ..logging import configure as configure_logging, get_logger
from ..probe import probe_asset
from .worker import execute

log = get_logger(__name__)

#: How long to wait when there was nothing to do. Short enough that submitting
#: a job in the browser feels like it started, long enough not to hold a
#: connection busy all afternoon.
IDLE_SLEEP_S = 2.0


def _queued(engine, limit: int = 1) -> list[tuple[str, str]]:
    """(org_id, job_id) for the oldest queued jobs. The privileged read."""
    with engine.connect() as conn:
        return [
            (row.org_id, row.id)
            for row in conn.execute(
                sa.text(
                    "SELECT org_id, id FROM jobs WHERE status = 'queued' "
                    "ORDER BY created_at LIMIT :n"
                ),
                {"n": limit},
            )
        ]


def _unprobed(engine, limit: int = 1) -> list[tuple[str, str]]:
    """(org_id, asset_id) for the oldest assets awaiting stage 0.

    `probing` is only reached from `complete_upload`, so the object is whole by
    the time a row appears here — there is no race with the browser to lose.
    """
    with engine.connect() as conn:
        return [
            (row.org_id, row.id)
            for row in conn.execute(
                sa.text(
                    "SELECT org_id, id FROM assets WHERE status = 'probing' "
                    "ORDER BY created_at LIMIT :n"
                ),
                {"n": limit},
            )
        ]


def _fail(org_id: str, job_id: str, reason: str) -> None:
    """Mark a job that could not even be started — and give the hold back.

    `worker.execute` handles a failure during a run and on the completion path
    after it. A failure before either — media that is not where the row says it
    is, a workspace that cannot be created — raises out of it, and a job left
    `queued` is picked up again on the next poll, forever, failing the same way
    every two seconds.

    The release is not redundant with the worker's. The hold is placed at
    *submission*, by the API, so a job that dies before `execute` gets as far as
    its own failure handling has already taken the customer's credits and has no
    one to give them back. This used to set the status alone: the job read
    `failed` on the jobs page while the balance still showed the cap held, with
    nothing in the ledger to explain it and no path back except a hand-written
    row. Every terminal status this process writes now moves the money with it.

    `release` is idempotent and refuses after a settle, so calling it here when
    `execute` already released costs a query and changes nothing.
    """
    try:
        with session_for_org(org_id) as s:
            cap = _approved_cap(s, org_id, job_id)
            if cap:
                job_writes.release(s, org_id, job_id, cap, reason="job failed")
            job_writes.set_status(s, org_id, job_id, "failed",
                                  error={"code": reason},
                                  finished_at=sa.func.now())
    except Exception:  # noqa: BLE001 — nothing left to do about it
        log.error("devrunner.could_not_mark_failed", job_id=job_id)


def _approved_cap(s, org_id: str, job_id: str) -> float:
    """What the customer approved at submission, which is what is held."""
    jobs = m.Job.__table__
    cap = s.execute(
        sa.select(jobs.c.approved_cap).where(
            jobs.c.org_id == org_id, jobs.c.id == job_id
        )
    ).scalar()
    return float(cap or 0)


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(
        prog="python -m mishne.orchestration.devrunner",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--once", action="store_true",
                        help="run whatever is queued now, then stop")
    parser.add_argument("--poll", type=float, default=IDLE_SLEEP_S)
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    if settings.environment != "local":
        print(f"refusing to run in environment={settings.environment!r}. "
              "Outside local, Step Functions runs jobs — see "
              "orchestration/statemachine.py.")
        return 1

    engine = sa.create_engine(normalise_url(settings.database_url))
    running = True

    #: The job `execute` is inside, if there is one. The signal handler reads it
    #: so that what it prints is true. `dev.sh` runs this under a file watcher
    #: which stops it with SIGINT on every source change, so the line is read
    #: many times a day and "finishing the current job" while idle reads as a
    #: runner that is doing something and will not let go.
    in_flight: str | None = None

    def stop(*_signal) -> None:
        nonlocal running
        running = False
        if in_flight:
            print("\nfinishing the current job, then stopping…")
        else:
            print("\nstopping…")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    #: Assets this process has already tried and that are somehow still
    #: `probing` — `probe_asset` writes a terminal status either way, so this
    #: only fills up if the write itself failed. Without it, that becomes a hot
    #: loop on the same row rather than one logged line.
    attempted: set[str] = set()

    print("waiting for uploads to probe and jobs to run "
          "— upload or submit in the browser (ctrl-c to stop)")
    while running:
        # Probing first: an asset nothing has read yet has no duration, so it
        # has no price, so it cannot be part of a job anybody is waiting on.
        probed = False
        for org_id, asset_id in _unprobed(engine):
            if asset_id in attempted:
                continue
            attempted.add(asset_id)
            log.info("devrunner.probing", asset_id=asset_id, org_id=org_id)
            try:
                status = probe_asset(org_id, asset_id, settings)
            except Exception as exc:  # noqa: BLE001 — `probe_asset` marks the row
                log.error("devrunner.probe_could_not_start", asset_id=asset_id,
                          reason=type(exc).__name__)
                continue
            probed = True
            print(f"{asset_id}: {status}")

        jobs = _queued(engine)
        if not jobs:
            if args.once:
                # There may be another asset behind the one just probed; keep
                # draining, and stop on the pass that finds nothing at all.
                if probed:
                    continue
                break
            if not probed:
                time.sleep(args.poll)
            continue
        for org_id, job_id in jobs:
            log.info("devrunner.starting", job_id=job_id, org_id=org_id)
            in_flight = job_id
            try:
                status = execute(org_id, job_id, settings)
            except Exception as exc:  # noqa: BLE001 — see `_fail`
                log.error("devrunner.job_could_not_start", job_id=job_id,
                          reason=type(exc).__name__)
                _fail(org_id, job_id, type(exc).__name__)
                continue
            finally:
                in_flight = None
            print(f"{job_id}: {status}")
        if args.once:
            break
    engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
