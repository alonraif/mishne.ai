"""`GET /v1/assets/{id}/proxy` — the states the editor polls through.

The player is welded to the transcript, so this endpoint is on the path of every
editing session. What it must never do is make "the preview is still encoding"
into an error: the screen asks repeatedly while the transcode runs, and a client
that has to read exception bodies to discover that nothing is wrong is a client
that will get it wrong. See ADR-0020.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("boto3")
pytest.importorskip("moto")
sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, PROJECT, digest, requires_schema, send_parts  # noqa: E402
from mishne import storage  # noqa: E402

pytestmark = requires_schema


def _asset(http, client, filename: str = "A002.mov") -> str:
    blob = b"v" * 4096
    created = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": filename, "bytes": len(blob), "checksum": digest(blob)},
    )
    assert created.status_code == 201, created.text
    created = created.json()
    key = f"orgs/{ORG}/projects/{PROJECT}/assets/{created['asset_id']}/source"
    parts = send_parts(client, key, created["upload_id"], blob, created["part_size"])
    http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})
    return created["asset_id"]


def _set(owner, asset_id: str, **values) -> None:
    sets = ", ".join(f"{k} = :{k}" for k in values)
    with owner.begin() as conn:
        conn.execute(
            sa.text(f"UPDATE assets SET {sets} WHERE id = :a"),
            {"a": asset_id, **values},
        )


@pytest.mark.parametrize("state", ["none", "pending", "running", "failed", "unsupported"])
def test_every_unfinished_state_is_an_answer_and_not_an_error(api, owner, state):
    """The editor polls this. A 409 for "not yet" would make the ordinary case
    an exception, and the poll loop in `use-api.ts` stops on any non-2xx."""
    http, client = api
    asset_id = _asset(http, client)
    _set(owner, asset_id, proxy_status=state)

    r = http.get(f"/v1/assets/{asset_id}/proxy")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == state
    assert r.json()["url"] is None



def test_a_ready_preview_hands_back_a_url_that_is_not_an_attachment(api, owner):
    """`Content-Disposition: attachment` is what makes a download a download.
    A `<video>` pointed at one downloads the file instead of playing it."""
    http, client = api
    asset_id = _asset(http, client)
    key = storage.derived_key(ORG, PROJECT, asset_id, "proxy.mp4")
    client.put_object(Bucket="test-derived", Key=key, Body=b"\x00" * 32)
    _set(owner, asset_id, proxy_status="ready", proxy_kind="video",
         proxy_s3_key=key, proxy_bytes=32)

    body = http.get(f"/v1/assets/{asset_id}/proxy").json()
    assert body["status"] == "ready"
    assert body["kind"] == "video"
    assert body["url"]
    assert "attachment" not in body["url"].lower()
    # Held inside a media element for a working session, not spent immediately.
    assert body["expires_in_s"] > 900


def test_a_row_whose_object_has_expired_does_not_promise_a_url(api, owner):
    """The derived bucket expires on its own clock. Answering `ready` with a
    key that is no longer there hands back a URL that 404s inside the player,
    which surfaces as a decode error and explains nothing."""
    http, client = api
    asset_id = _asset(http, client)
    _set(owner, asset_id, proxy_status="ready", proxy_kind="video",
         proxy_s3_key=None)

    body = http.get(f"/v1/assets/{asset_id}/proxy").json()
    assert body["status"] == "unsupported"
    assert body["url"] is None


def test_minting_a_preview_url_is_audited(api, owner):
    """It grants read access to the customer's footage to anyone holding it,
    for six hours. Who asked, and when, has to have an answer."""
    http, client = api
    asset_id = _asset(http, client)
    key = storage.derived_key(ORG, PROJECT, asset_id, "proxy.mp4")
    client.put_object(Bucket="test-derived", Key=key, Body=b"\x00" * 32)
    _set(owner, asset_id, proxy_status="ready", proxy_kind="video",
         proxy_s3_key=key, proxy_bytes=32)

    # Counted rather than compared: an asset id is its content hash, so every
    # test in this file that uploads the same bytes gets the same id, and the
    # audit log is append-only and survives `purge_org`. What is being asserted
    # is that this request wrote one row, not that the table was empty first.
    def issued() -> int:
        with owner.begin() as conn:
            return conn.execute(
                sa.text(
                    "SELECT count(*) FROM audit_log WHERE org_id = :o "
                    "AND action = 'asset.proxy_issued' AND resource_id = :a"
                ),
                {"o": ORG, "a": asset_id},
            ).scalar_one()

    before = issued()
    http.get(f"/v1/assets/{asset_id}/proxy")
    assert issued() == before + 1


def test_another_tenant_cannot_reach_this_preview(api, owner, other_tenant):
    """RLS is the backstop; this is the answer above it.

    `other_tenant` is a real session belonging to a real second organisation —
    a made-up org id would prove only that the org is unknown.
    """
    http, client = api
    asset_id = _asset(http, client)
    key = storage.derived_key(ORG, PROJECT, asset_id, "proxy.mp4")
    client.put_object(Bucket="test-derived", Key=key, Body=b"\x00" * 32)
    _set(owner, asset_id, proxy_status="ready", proxy_kind="video",
         proxy_s3_key=key, proxy_bytes=32)

    r = http.get(
        f"/v1/assets/{asset_id}/proxy",
        headers={"Authorization": f"Bearer {other_tenant}"},
    )
    assert r.status_code == 404, r.text
