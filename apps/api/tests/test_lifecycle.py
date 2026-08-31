"""Bucket lifecycle rules, including the one that stops a silent bill.

Parts of an abandoned multipart upload do not appear in the console, are not
returned by ListObjects, and are billed until something aborts them. The rule
that does that is the whole reason this file exists; the expiry rules are
checked alongside it because they are applied by the same code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

REGION = "eu-west-1"

# infra/ is not a package and is deliberately not importable as one — it is a
# deployment script that happens to be Python.
_INFRA = Path(__file__).resolve().parents[3] / "infra"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _INFRA / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lifecycle = _load("s3_lifecycle")
cors = _load("s3_cors")


@pytest.fixture
def client(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with moto.mock_aws():
        c = boto3.client("s3", region_name=REGION)
        for bucket in ("test-raw", "test-derived", "test-artifacts"):
            c.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
            )
        yield c


def _ids(client, bucket) -> set[str]:
    return {r["ID"] for r in lifecycle.current(client, bucket)}


def test_every_bucket_aborts_incomplete_multipart_uploads(client):
    for which, bucket in (("raw", "test-raw"), ("derived", "test-derived"),
                          ("artifacts", "test-artifacts")):
        lifecycle.apply(client, bucket, which)
        rules = {r["ID"]: r for r in lifecycle.current(client, bucket)}
        abort = rules["abort-incomplete-multipart-uploads"]
        assert abort["Status"] == "Enabled"
        assert abort["AbortIncompleteMultipartUpload"]["DaysAfterInitiation"] == 7


def test_the_deliverable_outlives_the_media_it_was_cut_from(client):
    lifecycle.apply(client, "test-raw", "raw")
    lifecycle.apply(client, "test-artifacts", "artifacts")

    raw = {r["ID"]: r for r in lifecycle.current(client, "test-raw")}
    artifacts = {r["ID"]: r for r in lifecycle.current(client, "test-artifacts")}

    assert (
        artifacts["expire-artifacts"]["Expiration"]["Days"]
        > raw["expire-raw-media"]["Expiration"]["Days"]
    )


def test_derived_files_go_first_because_they_are_rebuildable(client):
    lifecycle.apply(client, "test-derived", "derived")
    lifecycle.apply(client, "test-artifacts", "artifacts")
    derived = {r["ID"]: r for r in lifecycle.current(client, "test-derived")}
    artifacts = {r["ID"]: r for r in lifecycle.current(client, "test-artifacts")}
    assert (
        derived["expire-derived"]["Expiration"]["Days"]
        < artifacts["expire-artifacts"]["Expiration"]["Days"]
    )


def test_old_versions_are_expired_too(client):
    # Versioning is on, so an expiry leaves a delete marker and a noncurrent
    # version that keeps costing money on its own.
    lifecycle.apply(client, "test-raw", "raw")
    rules = {r["ID"]: r for r in lifecycle.current(client, "test-raw")}
    assert rules["expire-raw-media"]["NoncurrentVersionExpiration"]["NoncurrentDays"] > 0


def test_applying_twice_changes_nothing_the_second_time(client):
    assert lifecycle.apply(client, "test-raw", "raw") is True
    assert lifecycle.apply(client, "test-raw", "raw") is False


def test_a_rule_that_is_not_in_this_file_is_removed(client):
    # PutBucketLifecycleConfiguration replaces rather than merges, and this file
    # is the source of truth. A rule added by hand in the console vanishes on
    # the next deploy, which is intended and worth being sure of.
    client.put_bucket_lifecycle_configuration(
        Bucket="test-raw",
        LifecycleConfiguration={
            "Rules": [
                {"ID": "someone-did-this-by-hand", "Status": "Enabled",
                 "Filter": {"Prefix": ""}, "Expiration": {"Days": 1}}
            ]
        },
    )
    lifecycle.apply(client, "test-raw", "raw")
    assert "someone-did-this-by-hand" not in _ids(client, "test-raw")


def test_apply_all_reports_what_it_changed(client):
    changed = lifecycle.apply_all(
        client,
        {"raw": "test-raw", "derived": "test-derived", "artifacts": "test-artifacts"},
    )
    assert changed == {"test-raw": True, "test-derived": True, "test-artifacts": True}
    assert lifecycle.apply_all(
        client,
        {"raw": "test-raw", "derived": "test-derived", "artifacts": "test-artifacts"},
    ) == {"test-raw": False, "test-derived": False, "test-artifacts": False}


def test_an_unknown_bucket_role_is_refused_rather_than_left_unprotected(client):
    with pytest.raises(ValueError):
        lifecycle.rules_for("scratch")


# ─────────────────────────────────────────────────────────────────────── CORS


def test_the_etag_is_exposed_or_no_upload_can_ever_be_completed(client):
    # Every part uploads perfectly, the ETag is invisible to script, and the
    # completion fails with the whole file already sent. This one header is the
    # difference.
    cors.apply(client, "test-raw", "raw", ["https://app.example.tv"])
    rules = cors.current(client, "test-raw")
    assert rules[0]["ExposeHeaders"] == ["ETag"]


def test_the_upload_bucket_takes_puts_and_the_others_do_not(client):
    cors.apply(client, "test-raw", "raw", ["https://app.example.tv"])
    cors.apply(client, "test-artifacts", "artifacts", ["https://app.example.tv"])

    assert "PUT" in cors.current(client, "test-raw")[0]["AllowedMethods"]
    assert "PUT" not in cors.current(client, "test-artifacts")[0]["AllowedMethods"]


def test_origins_are_named_never_a_wildcard(client):
    cors.apply(client, "test-raw", "raw", ["https://app.example.tv"])
    assert cors.current(client, "test-raw")[0]["AllowedOrigins"] == ["https://app.example.tv"]
    for which in ("raw", "derived", "artifacts"):
        assert "*" not in cors.rules_for(which, ["https://app.example.tv"])[0]["AllowedOrigins"]


def test_applying_cors_twice_changes_nothing_the_second_time(client):
    origins = ["https://app.example.tv"]
    assert cors.apply(client, "test-raw", "raw", origins) is True
    assert cors.apply(client, "test-raw", "raw", origins) is False


def test_the_preflight_is_cached_long_enough_for_a_thousand_parts(client):
    # A 60 GB upload is ~960 parts. Preflighting each one is 960 extra round
    # trips on a link that is already the bottleneck.
    assert cors.rules_for("raw", ["https://app.example.tv"])[0]["MaxAgeSeconds"] >= 3600


# ── MinIO is S3-compatible, not S3 ─────────────────────────────────────────
#
# `PutBucketCors` comes back `NotImplemented` from MinIO, which crashed the
# whole setup script on the first machine that ran it — after the buckets had
# been created, so the run looked half-finished and the next step never ran.
#
# The rule is narrow: an unimplemented operation is tolerated only when the
# client is pointed at a non-AWS endpoint, which `Settings` refuses outside
# `environment=local`. Against real S3 the same error still stops everything,
# because there it means customer media has no expiry rule and the browser is
# not allowed to upload.

s3_local = _load("s3_local")


class _Unsupported(Exception):
    """What botocore raises for an operation the endpoint does not have."""

    def __init__(self, code: str = "NotImplemented"):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _MinioLike:
    """A client that answers every configuration call the way MinIO does."""

    class exceptions:  # noqa: N801 — mirrors botocore's shape
        ClientError = _Unsupported

    def get_bucket_cors(self, **_kw):
        raise _Unsupported()

    def put_bucket_cors(self, **_kw):
        raise _Unsupported()

    def get_bucket_lifecycle_configuration(self, **_kw):
        raise _Unsupported()

    def put_bucket_lifecycle_configuration(self, **_kw):
        raise _Unsupported()


def test_cors_against_minio_is_skipped_not_fatal(capsys):
    changed = cors.apply(_MinioLike(), "mishne-dev-raw", "raw",
                         ["http://localhost:3000"],
                         endpoint_url="http://localhost:9000")

    assert changed is False
    assert "not supported" in capsys.readouterr().out


def test_lifecycle_against_minio_is_skipped_not_fatal(capsys):
    changed = lifecycle.apply(_MinioLike(), "mishne-dev-raw", "raw",
                              endpoint_url="http://localhost:9000")

    assert changed is False
    assert "not supported" in capsys.readouterr().out


def test_the_same_error_from_real_s3_still_stops_everything():
    """No endpoint override means AWS, where an unconfigured bucket is a
    retention promise not being kept and a browser that cannot upload."""
    with pytest.raises(_Unsupported):
        cors.apply(_MinioLike(), "mishne-prod-raw", "raw",
                   ["https://app.mishne.ai"], endpoint_url="")
    with pytest.raises(_Unsupported):
        lifecycle.apply(_MinioLike(), "mishne-prod-raw", "raw", endpoint_url="")


def test_a_real_error_from_a_local_endpoint_still_raises():
    """Only *unimplemented* is tolerated. An access-denied or a missing bucket
    against MinIO is a broken local setup and should say so."""
    class Denied(_MinioLike):
        def get_bucket_cors(self, **_kw):
            raise _Unsupported("AccessDenied")

    with pytest.raises(_Unsupported):
        cors.apply(Denied(), "mishne-dev-raw", "raw", ["http://localhost:3000"],
                   endpoint_url="http://localhost:9000")


def test_a_refused_rule_is_named(capsys):
    """MinIO's whole answer is "the XML you provided was not well-formed or did
    not validate against our published schema", which does not say which rule.
    Guessing at four rules by deleting them one at a time is the afternoon this
    avoids."""
    class RefusesNoncurrent(_MinioLike):
        def get_bucket_lifecycle_configuration(self, **_kw):
            return {"Rules": []}

        def put_bucket_lifecycle_configuration(self, *, LifecycleConfiguration, **_kw):
            rules = LifecycleConfiguration["Rules"]
            if any("NoncurrentVersionExpiration" in r for r in rules):
                raise _Unsupported("InvalidArgument")

    changed = lifecycle.apply(RefusesNoncurrent(), "mishne-dev-raw", "raw",
                              endpoint_url="http://localhost:9000")

    out = capsys.readouterr().out
    assert changed is False
    assert "expire-raw-media" in out
    # And not the rule the endpoint was happy with.
    assert "abort-incomplete-multipart-uploads" not in out


def test_a_refused_configuration_still_raises_against_real_s3():
    class Refuses(_MinioLike):
        def get_bucket_lifecycle_configuration(self, **_kw):
            return {"Rules": []}

        def put_bucket_lifecycle_configuration(self, **_kw):
            raise _Unsupported("InvalidArgument")

    with pytest.raises(_Unsupported):
        lifecycle.apply(Refuses(), "mishne-prod-raw", "raw", endpoint_url="")


def test_local_buckets_are_versioned_like_the_deployed_ones():
    """The lifecycle rules expire noncurrent versions, which is not a
    meaningful thing to ask of an unversioned bucket — and a delete against a
    versioned bucket writes a marker rather than removing the object, which is
    different behaviour for the retention path to meet."""
    buckets = _load("s3_buckets")
    source = (_INFRA / "s3_buckets.py").read_text()
    assert "put_bucket_versioning" in source
    assert '"Status": "Enabled"' in source
    assert buckets is not None
