"""Object storage, against a real S3 protocol implementation in process.

`moto` rather than a mock of boto3: the interesting failures in this module are
S3's rules, not ours — a part below the 5 MiB minimum, part numbers that do not
ascend, an upload id that no longer exists — and a hand-written double asserts
that we called the methods we already know we called.

Nothing here needs AWS, a network, or credentials.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from mishne import storage  # noqa: E402
from mishne.config import Settings, get_settings  # noqa: E402

REGION = "eu-west-1"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="local",
        aws_region=REGION,
        s3_bucket_raw="test-raw",
        s3_bucket_derived="test-derived",
        s3_bucket_artifacts="test-artifacts",
        presign_ttl_seconds=900,
    )


@pytest.fixture
def aws(monkeypatch):
    """Fake credentials, so a stray real one can never be picked up here."""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SECURITY_TOKEN",
                 "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    # And no endpoint: a developer's `.env` points S3 at local MinIO, and a
    # client built with an explicit endpoint reaches it rather than moto. The
    # cache clear is what makes that setenv reach `get_client`, which asks
    # `get_settings()` — memoised, and quite possibly already holding the file.
    monkeypatch.setenv("S3_ENDPOINT_URL", "")
    get_settings.cache_clear()
    with moto.mock_aws():
        yield


@pytest.fixture
def store(aws, settings) -> storage.Storage:
    # `storage.get_client`, not a bare boto3 client: the signature version and
    # the addressing style are part of what is under test, and a client built
    # with boto3's defaults signs with SigV2 — which every bucket created after
    # 2018 rejects, at upload time, long after this code has returned.
    storage.get_client.cache_clear()
    client = storage.get_client()
    for bucket in ("test-raw", "test-derived", "test-artifacts"):
        client.create_bucket(
            Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
    yield storage.Storage(settings, client=client)
    storage.get_client.cache_clear()


# ────────────────────────────────────────────────────────────── the part layout


def test_an_empty_file_is_one_part_not_zero():
    # S3 refuses to complete an upload with no parts, and an empty file is a
    # thing a user can select by accident. It should fail at probe, with a
    # message about having no audio, rather than as a signature mismatch.
    assert storage.part_count(0) == 1


@pytest.mark.parametrize(
    "size, expected",
    [
        (1, 1),
        (storage.DEFAULT_PART_SIZE, 1),
        (storage.DEFAULT_PART_SIZE + 1, 2),
        (storage.DEFAULT_PART_SIZE * 3, 3),
    ],
)
def test_part_count_covers_the_file_exactly(size, expected):
    assert storage.part_count(size) == expected


def test_part_size_grows_rather_than_the_count_exceeding_s3s_ceiling():
    # Ten thousand parts is not a degraded upload; it is a failure at
    # CompleteMultipartUpload with the whole file already sent.
    huge = storage.DEFAULT_PART_SIZE * storage.MAX_PARTS * 4
    size = storage.choose_part_size(huge)
    assert size > storage.DEFAULT_PART_SIZE
    assert storage.part_count(huge, size) <= storage.MAX_PARTS


def test_every_part_size_stays_above_s3s_minimum():
    for size in (1, 10**6, 10**9, 10**12, 10**13):
        assert storage.choose_part_size(size) >= storage.MIN_PART_SIZE


# ─────────────────────────────────────────────────────────────── the key scheme


def test_keys_lead_with_the_tenant():
    # Every lifecycle rule, every retention deletion and every per-org IAM
    # policy is then a prefix, not a tag scan.
    key = storage.source_key("org_7fa2", "prj_promo", "ast_1")
    assert key.startswith(storage.org_prefix("org_7fa2"))
    assert storage.derived_key("org_7fa2", "p", "a", "audio.wav").startswith(
        storage.org_prefix("org_7fa2")
    )
    assert storage.artifact_key("org_7fa2", "job_1", "cut.aaf").startswith(
        storage.org_prefix("org_7fa2")
    )


def test_a_key_never_carries_the_customers_filename():
    key = storage.source_key("org_7fa2", "prj_promo", "ast_1")
    assert "PROMO_Q4_RAW_A002.mp4" not in key
    assert key.endswith("/source")


def test_the_source_key_is_stable_for_the_same_asset():
    # A retried upload must overwrite itself rather than leave an orphan.
    a = storage.source_key("org_7fa2", "prj_promo", "ast_1")
    b = storage.source_key("org_7fa2", "prj_promo", "ast_1")
    assert a == b


def test_content_id_wants_a_real_digest():
    digest = hashlib.sha256(b"rushes").hexdigest()
    assert storage.content_id(digest) == f"a_{digest[:24]}"
    with pytest.raises(ValueError):
        storage.content_id("not-a-digest")
    with pytest.raises(ValueError):
        storage.content_id(digest[:8])


def test_sha256_file_matches_hashlib(tmp_path: Path):
    blob = b"x" * (3 * 1024 * 1024) + b"tail"
    f = tmp_path / "rushes.mov"
    f.write_bytes(blob)
    assert storage.sha256_file(f, chunk=64 * 1024) == hashlib.sha256(blob).hexdigest()


# ──────────────────────────────────────────────────────────── multipart upload


def _upload_parts(store: storage.Storage, ref, upload_id, blob, part_size):
    """Stand in for the browser: send each slice, collect (number, etag)."""
    parts = []
    for i in range(storage.part_count(len(blob), part_size)):
        chunk = blob[i * part_size : (i + 1) * part_size]
        resp = store.client.upload_part(
            Bucket=ref.bucket, Key=ref.key, UploadId=upload_id,
            PartNumber=i + 1, Body=chunk,
        )
        parts.append((i + 1, resp["ETag"]))
    return parts


def test_a_multipart_upload_round_trips(store: storage.Storage, settings):
    ref = storage.ObjectRef(
        storage.bucket_for("raw", settings),
        storage.source_key("org_7fa2", "prj_promo", "ast_1"),
    )
    blob = b"a" * (storage.MIN_PART_SIZE + 17)
    part_size = storage.MIN_PART_SIZE

    upload_id = store.initiate_multipart(ref)
    parts = _upload_parts(store, ref, upload_id, blob, part_size)
    assert len(parts) == 2
    store.complete_multipart(ref, upload_id, parts)

    assert store.exists(ref)
    assert store.get_bytes(ref) == blob
    assert store.head(ref)["ContentLength"] == len(blob)


def test_completion_does_not_depend_on_the_order_parts_arrive_in(store, settings):
    # Parts are uploaded concurrently and retried out of order. An etag matched
    # to the wrong part number completes an upload whose bytes are in the wrong
    # places, and S3 reports nothing wrong.
    ref = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/a/source")
    first = b"1" * storage.MIN_PART_SIZE
    second = b"2" * 32
    upload_id = store.initiate_multipart(ref)
    parts = _upload_parts(store, ref, upload_id, first + second, storage.MIN_PART_SIZE)
    store.complete_multipart(ref, upload_id, list(reversed(parts)))
    assert store.get_bytes(ref) == first + second


def test_an_aborted_upload_leaves_nothing_to_pay_for(store, settings):
    ref = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/a/source")
    upload_id = store.initiate_multipart(ref)
    _upload_parts(store, ref, upload_id, b"z" * storage.MIN_PART_SIZE, storage.MIN_PART_SIZE)

    in_flight = store.client.list_multipart_uploads(Bucket=ref.bucket)
    assert len(in_flight.get("Uploads", [])) == 1

    store.abort_multipart(ref, upload_id)
    after = store.client.list_multipart_uploads(Bucket=ref.bucket)
    assert after.get("Uploads", []) == []
    assert not store.exists(ref)


def test_part_urls_cover_the_whole_file_and_nothing_more(store, settings):
    ref = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/a/source")
    total = storage.MIN_PART_SIZE * 2 + 11
    upload_id = store.initiate_multipart(ref)
    urls = store.part_urls(ref, upload_id, total, storage.MIN_PART_SIZE)

    assert [u.part_number for u in urls] == [1, 2, 3]
    assert urls[0].offset == 0
    assert sum(u.length for u in urls) == total
    for previous, following in zip(urls, urls[1:]):
        assert following.offset == previous.offset + previous.length


def test_a_presigned_part_url_is_signed_scoped_and_short_lived(store, settings):
    ref = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/a/source")
    upload_id = store.initiate_multipart(ref)
    url = store.part_urls(ref, upload_id, 10, storage.MIN_PART_SIZE)[0].url

    query = parse_qs(urlparse(url).query)
    assert query["partNumber"] == ["1"]
    assert query["uploadId"] == [upload_id]
    assert query["X-Amz-Expires"] == [str(settings.presign_ttl_seconds)]
    # SigV4. A SigV2 signature is rejected outright by any bucket made after
    # 2018, and the failure arrives at upload time.
    assert "X-Amz-Signature" in query
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]


def test_a_download_url_names_the_file_without_putting_it_in_the_path(store, settings):
    ref = storage.ObjectRef(storage.bucket_for("artifacts", settings), "orgs/o/jobs/j/a/cut.aaf")
    store.put_bytes(b"AAF", ref)
    url = store.presigned_get(ref, filename="Q4 promo rough cut.aaf")
    query = parse_qs(urlparse(url).query)
    assert "Q4 promo rough cut.aaf" in query["response-content-disposition"][0]
    assert "Q4%20promo" not in urlparse(url).path


def test_delete_prefix_removes_one_tenants_objects_and_says_how_many(store, settings):
    bucket = storage.bucket_for("raw", settings)
    for i in range(3):
        store.put_bytes(b"x", storage.ObjectRef(bucket, f"orgs/org_a/projects/p/assets/{i}/source"))
    store.put_bytes(b"x", storage.ObjectRef(bucket, "orgs/org_b/projects/p/assets/1/source"))

    removed = store.delete_prefix(bucket, storage.org_prefix("org_a"))
    assert removed == 3
    assert store.exists(storage.ObjectRef(bucket, "orgs/org_b/projects/p/assets/1/source"))


def test_head_and_get_are_none_when_the_object_is_not_there(store, settings):
    missing = storage.ObjectRef(storage.bucket_for("raw", settings), "orgs/o/p/nope/source")
    assert store.head(missing) is None
    assert store.get_bytes(missing) is None
    assert store.exists(missing) is False
