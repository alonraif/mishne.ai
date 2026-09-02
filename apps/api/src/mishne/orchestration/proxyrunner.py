"""The preview fleet: find work, and hand it to `proxyworker`.

    python -m mishne.orchestration.proxyrunner            # local: poll the table
    python -m mishne.orchestration.proxyrunner --serve    # production: drain the queue

## Why this is a process of its own

A preview is not a pipeline stage (ADR-0020) and it must not run where the API
runs (ADR-0021). ffmpeg over a three-hour master uses every core it is given for
as long as it takes; that is what a transcoder is supposed to do and it is an
outage on a box that is answering requests. So the transcode gets its own
process here and its own machine in production, and this module is the part that
differs between the two — `proxyworker.build_proxy` is identical either way.

It is also deliberately not folded into `devrunner`. That loop probes, then runs
one job, then probes again; a ten-minute transcode dropped into it would drain
the preview queue only while no job was running, which is exactly backwards.

## Two ways of finding work, one record of it

`assets.proxy_status = 'pending'` is the durable record that a preview is owed,
written in the same transaction as the probe result. What differs is how a
worker hears about it:

* **local** — poll the table. On one machine the row is the queue and nothing
  else is needed.
* **`--serve`** — long-poll SQS. A message is a wake-up carrying two ids
  (`preview_dispatch`).

**The sweep is what makes the second one safe.** A queue message can be lost —
a send that failed after the row was committed, a consumer that crashed between
receive and claim — and the row would then sit `pending` with nobody coming.
Every `preview_sweep_seconds` this reclaims leases whose worker died and picks
up rows that have been waiting too long, so a dropped message costs latency
rather than a preview that never arrives.

## The privileged read

Row-level security fails closed on an unset `app.org_id`, so "find every asset
waiting for a preview" is a question no tenant-scoped session can ask. Like
`devrunner`, this uses the owner connection for one SELECT of two id columns;
the work itself runs in ordinary tenant-scoped sessions subject to every policy.
That is why the polling mode refuses to run outside `environment=local` — in
production the queue is the source of work and the sweep is the backstop, not a
loop with a superuser connection in it.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

import sqlalchemy as sa

from ..config import Settings, get_settings, load_env_file
from ..db import uploads
from ..db.base import normalise_url
from ..logging import configure as configure_logging
from ..logging import get_logger
from . import preview_dispatch
from .proxyworker import build_proxy

log = get_logger(__name__)

#: Long enough not to hold a connection busy all afternoon, short enough that a
#: preview is under way before anyone has finished reading the upload screen.
IDLE_SLEEP_S = 3.0

#: SQS long-poll. Twenty seconds is the maximum and costs one request a
#: twentieth of a minute; short polling the same queue is the classic way to
#: spend more on empty receives than on the work.
WAIT_SECONDS = 20


def _pending(engine, limit: int = 1) -> list[tuple[str, str]]:
    """(org_id, asset_id) for the oldest assets waiting for a preview.

    Ordered by `created_at` against the partial index migration 0012 builds, so
    this stays an index scan rather than a sequential one over every asset.
    """
    with engine.connect() as conn:
        return [
            (row.org_id, row.id)
            for row in conn.execute(
                sa.text(
                    "SELECT org_id, id FROM assets WHERE proxy_status = 'pending' "
                    "ORDER BY created_at LIMIT :n"
                ),
                {"n": limit},
            )
        ]


def sweep(engine, settings: Settings) -> tuple[int, int]:
    """Reclaim dead workers' leases. Returns what `reclaim_stale_proxies` did.

    Kept as its own function because it is the one piece of the fleet that has
    to run whether or not anything is arriving on the queue: the rows it fixes
    are, by definition, ones nothing is going to mention again.
    """
    return uploads.reclaim_stale_proxies(
        engine,
        lease_seconds=settings.preview_lease_seconds,
        max_attempts=settings.preview_max_attempts,
    )


def _stopper() -> tuple[callable, callable]:
    """A ctrl-c that lets the current encode finish rather than orphaning it."""
    state = {"running": True}

    def stop(*_signal) -> None:
        state["running"] = False
        print("\nfinishing the current preview, then stopping…")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return (lambda: state["running"]), stop


def poll_forever(engine, settings: Settings, *, once: bool, delay: float) -> int:
    """Local mode: the table is the queue."""
    running, _ = _stopper()
    last_sweep = 0.0
    print("waiting for uploads to preview (ctrl-c to stop)")
    while running():
        now = time.monotonic()
        if now - last_sweep > settings.preview_sweep_seconds:
            sweep(engine, settings)
            last_sweep = now

        pending = _pending(engine)
        if not pending:
            if once:
                return 0
            time.sleep(delay)
            continue
        for org_id, asset_id in pending:
            log.info("proxyrunner.building", asset_id=asset_id, org_id=org_id)
            print(f"{asset_id}: preview {build_proxy(org_id, asset_id, settings)}")
    return 0


def serve_queue(engine, settings: Settings, *, once: bool, client=None) -> int:
    """Production mode: drain the queue, and sweep for what never arrived.

    A message is deleted once the work reaches a terminal state — including the
    states that mean "this will never encode", because leaving those on the
    queue is a worker picking up the same unreadable file every visibility
    timeout for ever. A message whose row was already claimed by somebody else
    is deleted too: losing the race is an ordinary outcome, not a failure.
    """
    if client is None:
        import boto3

        client = boto3.client("sqs")
    sqs = client
    running, _ = _stopper()
    last_sweep = 0.0
    print(f"draining {settings.preview_queue_url} (ctrl-c to stop)")

    while running():
        now = time.monotonic()
        if now - last_sweep > settings.preview_sweep_seconds:
            sweep(engine, settings)
            # Rows nothing is going to mention again — a notification that was
            # never sent, or was lost. The queue cannot know about these; the
            # table does.
            for org_id, asset_id in _pending(engine, limit=5):
                log.info("proxyrunner.swept", asset_id=asset_id)
                build_proxy(org_id, asset_id, settings)
            last_sweep = now

        received = sqs.receive_message(
            QueueUrl=settings.preview_queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0 if once else WAIT_SECONDS,
        )
        messages = received.get("Messages", [])
        if not messages:
            if once:
                return 0
            continue

        for message in messages:
            parsed = preview_dispatch.parse_message(message["Body"])
            if parsed is None:
                # Nothing can act on it, so it must not come back. Dropping is
                # the only alternative to an infinite redelivery loop.
                log.warning("proxyrunner.unreadable_message")
                sqs.delete_message(
                    QueueUrl=settings.preview_queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
                continue
            org_id, asset_id = parsed
            log.info("proxyrunner.building", asset_id=asset_id, org_id=org_id)
            status = build_proxy(org_id, asset_id, settings)
            print(f"{asset_id}: preview {status}")
            if status != "failed":
                sqs.delete_message(
                    QueueUrl=settings.preview_queue_url,
                    ReceiptHandle=message["ReceiptHandle"],
                )
        if once:
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path.cwd() / ".env")
    parser = argparse.ArgumentParser(
        prog="python -m mishne.orchestration.proxyrunner",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--once", action="store_true",
                        help="take whatever is waiting now, then stop")
    parser.add_argument("--serve", action="store_true",
                        help="drain the preview queue instead of polling the table")
    parser.add_argument("--poll", type=float, default=IDLE_SLEEP_S)
    args = parser.parse_args(argv)

    configure_logging()
    settings = get_settings()
    engine = sa.create_engine(normalise_url(settings.database_url))

    serve = args.serve or settings.preview_dispatch == "sqs"
    if not serve and settings.environment != "local":
        print(f"refusing to poll the database in environment="
              f"{settings.environment!r}. Outside local the preview fleet is fed "
              f"by a queue — set preview_dispatch=sqs, or pass --serve.")
        return 1
    if serve and not settings.preview_queue_url:
        print("preview_queue_url is not set, so there is no queue to drain.")
        return 1

    if serve:
        return serve_queue(engine, settings, once=args.once)
    return poll_forever(engine, settings, once=args.once, delay=args.poll)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
