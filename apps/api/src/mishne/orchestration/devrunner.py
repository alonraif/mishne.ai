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

## The one privileged read, and why it is bounded

Row-level security fails closed on an unset `app.org_id`, so "find every queued
job" is a question no tenant-scoped session can ask. This uses the owner
connection for exactly one SELECT of two id columns — `(org_id, id)` of queued
jobs — and nothing else. Execution then goes through `worker.execute`, which
opens ordinary tenant-scoped sessions and is subject to every policy in the
database, exactly as it is in production.

That is a deliberate, narrow exception and it is why this refuses to run
outside `environment=local`. In staging or production the answer is Step
Functions, not a loop with a superuser connection in it.

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
from ..logging import configure as configure_logging, get_logger
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


def _fail(org_id: str, job_id: str, reason: str) -> None:
    """Mark a job that could not even be started.

    `worker.execute` handles a failure *during* a run and releases the hold.
    A failure before that — media that is not where the row says it is, a
    workspace that cannot be created — raises out of it, and a job left
    `queued` is picked up again on the next poll, forever, failing the same way
    every two seconds.
    """
    try:
        with session_for_org(org_id) as s:
            job_writes.set_status(s, org_id, job_id, "failed",
                                  error={"code": reason},
                                  finished_at=sa.func.now())
    except Exception:  # noqa: BLE001 — nothing left to do about it
        log.error("devrunner.could_not_mark_failed", job_id=job_id)


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

    def stop(*_signal) -> None:
        nonlocal running
        running = False
        print("\nfinishing the current job, then stopping…")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print("waiting for queued jobs — submit one in the browser (ctrl-c to stop)")
    while running:
        jobs = _queued(engine)
        if not jobs:
            if args.once:
                break
            time.sleep(args.poll)
            continue
        for org_id, job_id in jobs:
            log.info("devrunner.starting", job_id=job_id, org_id=org_id)
            try:
                status = execute(org_id, job_id, settings)
            except Exception as exc:  # noqa: BLE001 — see `_fail`
                log.error("devrunner.job_could_not_start", job_id=job_id,
                          reason=type(exc).__name__)
                _fail(org_id, job_id, type(exc).__name__)
                continue
            print(f"{job_id}: {status}")
        if args.once:
            break
    engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
