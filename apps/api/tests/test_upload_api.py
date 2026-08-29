"""The upload endpoints, against a real database and a real S3 implementation.

Postgres because the write path's whole job is to leave the database and the
object store agreeing, and an in-memory double agrees with itself by
construction. moto because the failures worth catching are S3's rules.

Skips itself when there is no migrated schema — see conftest.
"""

from __future__ import annotations

import hashlib

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")
sa = pytest.importorskip("sqlalchemy")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from conftest import (  # noqa: E402
    ORG,
    PART_SIZE,
    PROJECT,
    asset_row,
    create_asset,
    digest,
    requires_schema,
    send_parts,
)

pytestmark = requires_schema


# ─────────────────────────────────────────────────────────────────── the happy path


def test_an_upload_goes_from_presign_to_a_probing_asset(api, owner):
    http, client = api
    blob = b"m" * (PART_SIZE * 2 + 128)

    created = create_asset(http, blob)
    assert created["total_parts"] == 3
    assert len(created["parts"]) == 3
    assert sum(p["length"] for p in created["parts"]) == len(blob)

    row = asset_row(owner, created["asset_id"])
    assert row.status == "uploading"
    assert row.upload_id == created["upload_id"]
    assert row.s3_key.startswith(f"orgs/{ORG}/projects/{PROJECT}/")
    # The rate is a placeholder until stage 0 runs, and it is meant to look it.
    assert (row.edit_rate_num, row.edit_rate_den) == (1, 1)
    assert row.probed_at is None

    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    done = http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})
    assert done.status_code == 200, done.text

    # `probing`, not `ready`: what is in the object is still a claim.
    assert done.json()["status"] == "probing"
    after = asset_row(owner, created["asset_id"])
    assert after.status == "probing"
    assert after.upload_id is None
    assert client.head_object(Bucket="test-raw", Key=row.s3_key)["ContentLength"] == len(blob)


def test_the_object_holds_exactly_what_was_uploaded(api, owner):
    http, client = api
    blob = bytes(range(256)) * 4096

    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})

    stored = client.get_object(Bucket="test-raw", Key=row.s3_key)["Body"].read()
    assert hashlib.sha256(stored).hexdigest() == digest(blob)


# ─────────────────────────────────────────────────────────────── retry and resume


def test_coming_back_to_a_part_finished_upload_keeps_the_parts_already_sent(api, owner):
    # The difference between an upload that resumes and one that merely says it
    # does. A refreshed page must not throw away hours of somebody's evening.
    http, client = api
    blob = b"r" * (PART_SIZE * 2 + 9)

    first = create_asset(http, blob)
    row = asset_row(owner, first["asset_id"])
    # One part, and then the laptop closes.
    sent = client.upload_part(
        Bucket="test-raw", Key=row.s3_key, UploadId=first["upload_id"],
        PartNumber=1, Body=blob[: first["part_size"]],
    )

    second = create_asset(http, blob)

    assert second["asset_id"] == first["asset_id"]
    assert second["upload_id"] == first["upload_id"]
    state = http.get(f"/v1/assets/{first['asset_id']}/upload-parts").json()
    assert [p["part_number"] for p in state["uploaded"]] == [1]
    assert state["uploaded"][0]["etag"] == sent["ETag"]
    assert state["total_parts"] == 3


def test_a_vanished_upload_is_replaced_rather_than_resumed_into_nothing(api, owner):
    http, client = api
    blob = b"gone" * 500
    first = create_asset(http, blob)
    row = asset_row(owner, first["asset_id"])
    # Something else aborted it: a lifecycle rule, or a cancel from another tab.
    client.abort_multipart_upload(
        Bucket="test-raw", Key=row.s3_key, UploadId=first["upload_id"]
    )

    second = create_asset(http, blob)

    assert second["asset_id"] == first["asset_id"]
    assert second["upload_id"] != first["upload_id"]
    in_flight = client.list_multipart_uploads(Bucket="test-raw").get("Uploads", [])
    assert [u["UploadId"] for u in in_flight] == [second["upload_id"]]


def test_upload_parts_is_a_conflict_once_the_upload_has_completed(api, owner):
    http, client = api
    blob = b"done" * 400
    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})

    assert http.get(f"/v1/assets/{created['asset_id']}/upload-parts").status_code == 409


def test_resume_hands_back_only_the_parts_that_are_asked_for(api, owner):
    http, _ = api
    blob = b"z" * (PART_SIZE * 2 + 1)

    created = create_asset(http, blob)
    resumed = http.post(
        f"/v1/assets/{created['asset_id']}/upload-urls", json={"part_numbers": [1]}
    )

    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert [p["part_number"] for p in body["parts"]] == [1]
    assert body["upload_id"] == created["upload_id"]
    assert body["total_parts"] == created["total_parts"]
    # The layout is recomputed from the stored size, so a part a client already
    # sent is still the same bytes.
    assert body["parts"][0]["offset"] == 0
    assert body["parts"][0]["length"] == created["parts"][0]["length"]


def test_resume_defaults_to_every_part(api):
    http, _ = api
    created = create_asset(http, b"q" * (PART_SIZE + 5))
    resumed = http.post(f"/v1/assets/{created['asset_id']}/upload-urls", json={})
    assert len(resumed.json()["parts"]) == created["total_parts"]


def test_resume_refuses_a_part_that_does_not_exist(api):
    http, _ = api
    created = create_asset(http, b"q" * 64)
    resp = http.post(
        f"/v1/assets/{created['asset_id']}/upload-urls", json={"part_numbers": [9999]}
    )
    assert resp.status_code == 422


# ───────────────────────────────────────────────────────────────────────── refusals


def test_a_second_upload_of_a_file_already_here_is_a_conflict(api, owner):
    http, client = api
    blob = b"done" * 500

    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})

    again = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "A002.mxf", "bytes": len(blob), "checksum": digest(blob)},
    )
    assert again.status_code == 409
    assert again.headers["X-Asset-Id"] == created["asset_id"]


def test_a_file_larger_than_the_ceiling_is_refused_before_anything_is_created(api, owner):
    http, client = api
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={
            "filename": "everything.mxf",
            "bytes": 1024**5,  # 1 PiB
            "checksum": digest(b"whatever"),
        },
    )
    assert resp.status_code == 413
    assert client.list_multipart_uploads(Bucket="test-raw").get("Uploads", []) == []


def test_a_checksum_that_is_not_a_digest_is_refused(api):
    http, _ = api
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "a.mxf", "bytes": 10, "checksum": "hello"},
    )
    assert resp.status_code == 422


def test_an_upload_into_someone_elses_project_is_a_404(api):
    http, _ = api
    resp = http.post(
        "/v1/projects/prj_not_mine/assets",
        json={"filename": "a.mxf", "bytes": 10, "checksum": digest(b"a")},
    )
    assert resp.status_code == 404


def test_completing_with_the_wrong_number_of_parts_is_refused(api, owner):
    # A short parts list means the client gave up somewhere and the object would
    # be missing its tail. S3 will assemble whatever it is given.
    http, client = api
    blob = b"p" * (PART_SIZE * 2 + 3)
    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    assert len(parts) == 3

    resp = http.post(
        f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts[:2]}
    )

    assert resp.status_code == 422
    assert asset_row(owner, created["asset_id"]).status == "uploading"


def test_completing_with_a_gap_in_the_part_numbers_is_refused(api, owner):
    # Right count, wrong parts: part 2 sent twice and part 3 never. The bytes
    # would be wrong and nothing downstream would know.
    http, client = api
    blob = b"g" * (PART_SIZE * 2 + 3)
    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])

    resp = http.post(
        f"/v1/assets/{created['asset_id']}/complete",
        json={"parts": [parts[0], parts[1], parts[1]]},
    )

    assert resp.status_code == 422
    assert asset_row(owner, created["asset_id"]).status == "uploading"


def test_completing_twice_is_a_conflict_not_a_second_completion(api, owner):
    http, client = api
    blob = b"once" * 300
    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])

    assert http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts}).status_code == 200
    again = http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})
    assert again.status_code == 409


# ────────────────────────────────────────────────────────────────────────── abort


def test_cancelling_stops_the_upload_and_removes_the_row(api, owner):
    http, client = api
    created = create_asset(http, b"c" * 4096)
    asset_id = created["asset_id"]

    resp = http.delete(f"/v1/assets/{asset_id}/upload")

    assert resp.status_code == 204
    assert asset_row(owner, asset_id) is None
    assert client.list_multipart_uploads(Bucket="test-raw").get("Uploads", []) == []


def test_cancelling_an_upload_that_has_completed_is_a_conflict(api, owner):
    http, client = api
    blob = b"kept" * 400
    created = create_asset(http, blob)
    row = asset_row(owner, created["asset_id"])
    parts = send_parts(client, row.s3_key, created["upload_id"], blob, created["part_size"])
    http.post(f"/v1/assets/{created['asset_id']}/complete", json={"parts": parts})

    resp = http.delete(f"/v1/assets/{created['asset_id']}/upload")

    assert resp.status_code == 409
    assert asset_row(owner, created["asset_id"]) is not None


# ─────────────────────────────────────────────────────────────── tenancy and mocks


def test_another_org_cannot_see_or_touch_this_ones_asset(api, owner, other_tenant):
    http, _ = api
    created = create_asset(http, b"mine" * 100)

    other = http.post(
        f"/v1/assets/{created['asset_id']}/upload-urls",
        json={},
        headers={"Authorization": f"Bearer {other_tenant}"},
    )

    # Not 403: an asset another tenant cannot see does not exist as far as they
    # are concerned, and a 403 confirms the id is real. Nothing in the router
    # filters by org — the empty result comes from the database.
    assert other.status_code == 404


def test_a_request_with_no_session_is_refused_before_anything_else(api):
    http, _ = api
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "a.mxf", "bytes": 10, "checksum": digest(b"a")},
        headers={"Authorization": ""},
    )
    assert resp.status_code == 401


def test_a_viewer_may_look_but_not_upload(api, viewer_token):
    http, _ = api
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={"filename": "a.mxf", "bytes": 10, "checksum": digest(b"a")},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403


def test_the_write_path_refuses_to_pretend_when_the_api_is_serving_fixtures(
    monkeypatch, clear_caches
):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "true")
    clear_caches()
    from mishne.main import app

    with TestClient(app) as http:
        resp = http.post(
            f"/v1/projects/{PROJECT}/assets",
            json={"filename": "a.mxf", "bytes": 10, "checksum": digest(b"a")},
            headers={"X-Org-Id": ORG},
        )

    assert resp.status_code == 503
