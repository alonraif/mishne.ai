"""Running the transcode somewhere else: leases, dispatch, and the consumer.

ffmpeg over a three-hour master saturates whatever it is given, so in production
previews are built by a fleet that is not the API. Everything here is a failure
that only appears once the worker is a different computer, and none of it is
visible on one machine. See ADR-0021.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("boto3")
pytest.importorskip("moto")
sa = pytest.importorskip("sqlalchemy")

import boto3  # noqa: E402
import moto  # noqa: E402

from conftest import ORG, PROJECT, requires_schema  # noqa: E402
from mishne.config import Settings  # noqa: E402
from mishne.db import uploads  # noqa: E402
from mishne.orchestration import preview_dispatch, proxyrunner  # noqa: E402

REGION = "eu-west-1"


# ────────────────────────────────────────────────────────────── the dispatch


def test_a_queue_message_carries_ids_and_nothing_else():
    """The same rule as the state machine: a queue is not a place for payloads,
    and everything a consumer needs is reachable from the two ids."""
    with moto.mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="previews")["QueueUrl"]
        preview_dispatch.SqsDispatch(queue_url=url, client=sqs).notify("org_a", "ast_b")

        body = json.loads(
            sqs.receive_message(QueueUrl=url)["Messages"][0]["Body"]
        )
    assert body == {"v": preview_dispatch.MESSAGE_VERSION,
                    "org_id": "org_a", "asset_id": "ast_b"}


def test_a_notification_that_fails_does_not_fail_the_upload():
    """The row is already committed and `pending`; the message is a wake-up.
    Raising here would fail an upload that succeeded, over a notification."""
    class Broken:
        def send_message(self, **_kw):
            raise RuntimeError("no queue today")

    # Does not raise. The fleet's sweep is what makes this survivable.
    preview_dispatch.SqsDispatch(queue_url="q", client=Broken()).notify("o", "a")


def test_a_message_nothing_can_act_on_is_dropped_rather_than_retried():
    """Anything unparseable would otherwise come back on every visibility
    timeout, for ever."""
    assert preview_dispatch.parse_message("not json") is None
    assert preview_dispatch.parse_message('{"org_id": "o"}') is None
    assert preview_dispatch.parse_message('{"org_id": 1, "asset_id": 2}') is None
    assert preview_dispatch.parse_message(
        '{"org_id": "o", "asset_id": "a"}') == ("o", "a")


def test_local_dispatch_sends_nothing_because_the_row_is_the_queue():
    assert isinstance(preview_dispatch.build(Settings()), preview_dispatch.LocalDispatch)
    preview_dispatch.LocalDispatch().notify("o", "a")


def test_sqs_without_a_queue_url_is_refused_at_boot():
    """Previews that never arrive, with nothing in the logs, is the failure this
    avoids."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(preview_dispatch="sqs", preview_queue_url="")


# ─────────────────────────────────────────────────────────────── the leases


def _asset(
    owner,
    asset_id: str,
    *,
    proxy_status: str = "pending",
    proxy_attempts: int = 0,
    claimed_ago_s: int | None = None,
) -> None:
    """One asset row, with its lease set to a given age.

    `claimed_ago_s` is seconds rather than an expression because `now() -
    interval` is SQL, and a driver cannot bind SQL as a parameter — it arrives
    as a string and the UPDATE fails on the type.
    """
    lease = (
        "NULL" if claimed_ago_s is None
        else f"now() - interval '{int(claimed_ago_s)} seconds'"
    )
    with owner.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO assets (id, org_id, project_id, kind, ingest_mode, "
                "status, filename, edit_rate_num, edit_rate_den) VALUES "
                "(:a, :o, :p, 'video', 'full_media', 'ready', 'x.mov', 25, 1)"
            ),
            {"a": asset_id, "o": ORG, "p": PROJECT},
        )
        conn.execute(
            sa.text(
                "UPDATE assets SET proxy_status = :s, proxy_attempts = :n, "
                f"       proxy_claimed_at = {lease} "
                " WHERE id = :a"
            ),
            {"a": asset_id, "s": proxy_status, "n": proxy_attempts},
        )


def _row(owner, asset_id: str):
    with owner.begin() as conn:
        return conn.execute(
            sa.text(
                "SELECT proxy_status, proxy_attempts, proxy_claimed_at "
                "FROM assets WHERE id = :a"
            ),
            {"a": asset_id},
        ).first()


@pytest.fixture
def engine(owner, tenant):
    return owner


def test_a_worker_that_died_gives_its_preview_back(engine):
    """`running` is a state nothing leaves on its own. A spot instance reclaimed
    mid-encode would otherwise leave a preview that never arrives and never
    says why."""
    _asset(engine, "ast_dead", proxy_status="running", proxy_attempts=1,
           claimed_ago_s=7200)

    requeued, abandoned = uploads.reclaim_stale_proxies(
        engine, lease_seconds=3600, max_attempts=3)

    assert (requeued, abandoned) == (1, 0)
    row = _row(engine, "ast_dead")
    assert row.proxy_status == "pending"
    assert row.proxy_claimed_at is None


def test_a_live_worker_keeps_its_preview(engine):
    _asset(engine, "ast_live", proxy_status="running", proxy_attempts=1,
           claimed_ago_s=30)

    assert uploads.reclaim_stale_proxies(
        engine, lease_seconds=3600, max_attempts=3) == (0, 0)
    assert _row(engine, "ast_live").proxy_status == "running"


def test_media_that_will_never_encode_stops_costing_cpu(engine):
    """Reclaiming means a row can be tried again, and something has to stop
    'again' becoming 'for ever' on a file ffmpeg cannot read."""
    _asset(engine, "ast_poison", proxy_status="running", proxy_attempts=3,
           claimed_ago_s=7200)

    requeued, abandoned = uploads.reclaim_stale_proxies(
        engine, lease_seconds=3600, max_attempts=3)

    assert (requeued, abandoned) == (0, 1)
    row = _row(engine, "ast_poison")
    assert row.proxy_status == "failed"


def test_a_claim_without_a_lease_is_left_alone(engine):
    """A release that predates migration 0013 claims without stamping one.
    NULL means "cannot judge this", not "expired" — treating it as expired
    would steal work from a running older worker during a deploy (ADR-0012)."""
    _asset(engine, "ast_old", proxy_status="running", proxy_attempts=1,
           claimed_ago_s=None)

    assert uploads.reclaim_stale_proxies(
        engine, lease_seconds=1, max_attempts=3) == (0, 0)
    assert _row(engine, "ast_old").proxy_status == "running"


def test_two_workers_cannot_encode_the_same_asset(engine):
    """The guard that makes the queue safe to drain from more than one machine.
    Without it both spend ten minutes on the same three hours of footage."""
    from mishne.db.base import session_for_org

    _asset(engine, "ast_race")
    with session_for_org(ORG) as a:
        first = uploads.claim_proxy(a, ORG, "ast_race")
    with session_for_org(ORG) as b:
        second = uploads.claim_proxy(b, ORG, "ast_race")

    assert (first, second) == (True, False)
    row = _row(engine, "ast_race")
    assert row.proxy_status == "running"
    assert row.proxy_attempts == 1
    assert row.proxy_claimed_at is not None


# ───────────────────────────────────────────────────────────── the consumer


def _settings(url: str) -> Settings:
    return Settings(preview_dispatch="sqs", preview_queue_url=url,
                    environment="local")


def test_the_consumer_builds_what_the_queue_asks_for(monkeypatch):
    built: list[tuple[str, str]] = []
    monkeypatch.setattr(
        proxyrunner, "build_proxy",
        lambda org, asset, settings=None: built.append((org, asset)) or "ready")
    monkeypatch.setattr(proxyrunner, "sweep", lambda *a, **k: (0, 0))
    monkeypatch.setattr(proxyrunner, "_pending", lambda *a, **k: [])

    with moto.mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="previews")["QueueUrl"]
        preview_dispatch.SqsDispatch(queue_url=url, client=sqs).notify("org_x", "ast_y")

        proxyrunner.serve_queue(None, _settings(url), once=True, client=sqs)

        assert built == [("org_x", "ast_y")]
        # Done means gone: a message left on the queue is the same file picked
        # up again on every visibility timeout.
        assert "Messages" not in sqs.receive_message(QueueUrl=url)


def test_a_preview_that_failed_goes_back_on_the_queue(monkeypatch):
    """`failed` is infrastructure — a download that timed out, a disk that
    filled — and may well work next time. `unsupported` is a verdict and must
    not come back."""
    monkeypatch.setattr(proxyrunner, "sweep", lambda *a, **k: (0, 0))
    monkeypatch.setattr(proxyrunner, "_pending", lambda *a, **k: [])

    for status, expect_redelivery in (("failed", True), ("unsupported", False)):
        monkeypatch.setattr(
            proxyrunner, "build_proxy", lambda o, a, settings=None, s=status: s)
        with moto.mock_aws():
            sqs = boto3.client("sqs", region_name=REGION)
            url = sqs.create_queue(
                QueueName="q", Attributes={"VisibilityTimeout": "0"})["QueueUrl"]
            preview_dispatch.SqsDispatch(queue_url=url, client=sqs).notify("o", "a")

            proxyrunner.serve_queue(None, _settings(url), once=True, client=sqs)

            again = sqs.receive_message(QueueUrl=url)
            assert ("Messages" in again) is expect_redelivery, status


def test_an_unreadable_message_is_dropped(monkeypatch):
    monkeypatch.setattr(proxyrunner, "sweep", lambda *a, **k: (0, 0))
    monkeypatch.setattr(proxyrunner, "_pending", lambda *a, **k: [])
    monkeypatch.setattr(
        proxyrunner, "build_proxy",
        lambda *a, **k: pytest.fail("should not have been called"))

    with moto.mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(
            QueueName="q", Attributes={"VisibilityTimeout": "0"})["QueueUrl"]
        sqs.send_message(QueueUrl=url, MessageBody="{not json")

        proxyrunner.serve_queue(None, _settings(url), once=True, client=sqs)

        assert "Messages" not in sqs.receive_message(QueueUrl=url)


def test_the_sweep_picks_up_what_no_message_ever_mentioned(monkeypatch):
    """A send that failed after the row was committed. The queue cannot know
    about these; the table does, and this is why a lost message costs latency
    rather than a preview that never arrives."""
    built: list[str] = []
    monkeypatch.setattr(
        proxyrunner, "build_proxy",
        lambda org, asset, settings=None: built.append(asset) or "ready")
    monkeypatch.setattr(proxyrunner, "sweep", lambda *a, **k: (0, 0))
    monkeypatch.setattr(proxyrunner, "_pending",
                        lambda engine, limit=1: [("org_x", "ast_orphan")])

    with moto.mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="q")["QueueUrl"]
        proxyrunner.serve_queue(None, _settings(url), once=True, client=sqs)

    assert built == ["ast_orphan"]


pytestmark = requires_schema


def test_the_first_sweep_runs_on_a_machine_that_just_booted(monkeypatch):
    """The sweep must not wait for the machine's uptime to exceed the interval.

    `last_sweep` started at 0.0 and the gate was `monotonic() - last_sweep >
    sweep_seconds`, which on a fresh boot reads `uptime > 300` — so the first
    sweep was skipped for five minutes on a new host and ran immediately on a
    laptop that had been up for hours. The test above passed on a developer's
    machine and failed on a CI runner for exactly that reason, which is the
    cheap version of the production failure.

    The expensive version: this fleet runs on spot and is expected to be
    interrupted (ADR-0021). A worker whose life is shorter than the sweep
    interval would never sweep at all, and the sweep is what turns a lost
    message into latency rather than a preview that never arrives.

    `monotonic` is pinned low here — the state a machine is in for the first
    few minutes after boot, and the one a CI runner is always in.
    """
    built: list[str] = []
    monkeypatch.setattr(
        proxyrunner, "build_proxy",
        lambda org, asset, settings=None: built.append(asset) or "ready")
    monkeypatch.setattr(proxyrunner, "sweep", lambda *a, **k: (0, 0))
    monkeypatch.setattr(proxyrunner, "_pending",
                        lambda engine, limit=1: [("org_x", "ast_orphan")])
    monkeypatch.setattr(proxyrunner.time, "monotonic", lambda: 3.0)

    with moto.mock_aws():
        sqs = boto3.client("sqs", region_name=REGION)
        url = sqs.create_queue(QueueName="q")["QueueUrl"]
        proxyrunner.serve_queue(None, _settings(url), once=True, client=sqs)

    assert built == ["ast_orphan"], (
        "a worker on a freshly booted machine never swept; a preview whose "
        "message was lost would never be built"
    )
