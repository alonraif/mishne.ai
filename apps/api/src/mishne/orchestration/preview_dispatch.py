"""Telling something, somewhere, to build a preview.

## The problem this exists for

ffmpeg over a three-hour master will use every core it is given for as long as
it takes. That is correct behaviour for a transcoder and an outage for an API:
in production the preview must not run on a machine that is answering requests.
So the thing that *decides* a preview is needed and the thing that *builds* it
have to be able to be different computers, and this is the seam between them.

## The rule: the row is the queue, the message is a wake-up

`assets.proxy_status = 'pending'` is the durable record that a preview is owed.
It is written in the same transaction as the probe result, so it cannot be lost
and cannot disagree with the asset it belongs to. Only *after* that does anyone
send a message.

This ordering is the whole design, and inverting it is the classic dual-write
bug: publish first and a crash before the commit leaves a message for a row that
does not want one; make the message the only record and a dropped message is a
preview that never arrives, with nothing anywhere that knows. Here a lost
message costs latency and nothing else — the fleet's sweep finds rows that have
been `pending` too long and picks them up (`proxyrunner`).

That is also why `notify` is allowed to fail quietly. It is an optimisation over
the sweep, not the mechanism.

## Why a queue and not the state machine

Previews are deliberately not a pipeline stage (ADR-0020): nothing downstream
reads one, and putting them in the Step Functions graph would re-couple the
transcript's latency to the transcode's, which is the thing being avoided. A
queue with its own consumers scales on its own depth and can be starved,
throttled or drained without touching a job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from ..config import Settings, get_settings
from ..logging import get_logger

log = get_logger(__name__)

#: Bumped if the message body ever gains a field a consumer must understand.
#: Consumers ignore what they do not know (ADR-0012), so this is for humans
#: reading a dead-letter queue.
MESSAGE_VERSION = 1


class PreviewDispatch(Protocol):
    """Somewhere to say "this asset wants a preview"."""

    def notify(self, org_id: str, asset_id: str) -> None:
        """Best effort, always. The row is the record; see the module docstring."""


@dataclass
class LocalDispatch:
    """Nothing to send: the loop on this machine is already watching the table.

    Not a stub. On one machine the row *is* the queue, and a second channel
    saying the same thing would be a second thing to keep in step.
    """

    def notify(self, org_id: str, asset_id: str) -> None:
        return None


@dataclass
class SqsDispatch:
    """A message to the preview fleet.

    Carries identifiers and nothing else, for the same reason the state machine
    does (`orchestration/statemachine.py`): a queue is not a place to put a
    payload, and everything a consumer needs is reachable from the two ids.
    """

    queue_url: str
    client: object = None

    def __post_init__(self) -> None:
        if self.client is None:
            import boto3

            self.client = boto3.client("sqs")

    def notify(self, org_id: str, asset_id: str) -> None:
        body = json.dumps(
            {"v": MESSAGE_VERSION, "org_id": org_id, "asset_id": asset_id}
        )
        try:
            self.client.send_message(QueueUrl=self.queue_url, MessageBody=body)
        except Exception as exc:  # noqa: BLE001 — see the module docstring
            # The row is already `pending` and committed. Losing this costs the
            # sweep interval, so it is a warning and not a failure: raising here
            # would fail an upload that succeeded, over a notification.
            log.warning("preview.notify_failed", asset_id=asset_id,
                        reason=type(exc).__name__)


def build(settings: Settings) -> PreviewDispatch:
    if settings.preview_dispatch == "sqs":
        return SqsDispatch(queue_url=settings.preview_queue_url)
    return LocalDispatch()


@lru_cache
def get_dispatch() -> PreviewDispatch:
    return build(get_settings())


def parse_message(body: str) -> tuple[str, str] | None:
    """(org_id, asset_id) from a queue message, or None if it is not one.

    Returns rather than raises: a malformed body is a message to acknowledge and
    drop, not a reason to stop the consumer. Anything that cannot be acted on
    would otherwise come back on every visibility timeout for ever.
    """
    try:
        payload = json.loads(body)
        org_id, asset_id = payload["org_id"], payload["asset_id"]
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(org_id, str) or not isinstance(asset_id, str):
        return None
    return org_id, asset_id
