"""Bucket lifecycle: what is kept, what is thrown away, and what is never billed for.

Run against an environment's buckets after they are created, and again whenever
these rules change:

    python infra/s3_lifecycle.py --apply          # uses the API's settings
    python infra/s3_lifecycle.py                  # print what would change

Three buckets because the three lifecycles differ by more than an order of
magnitude in both directions (docs/architecture/04-security.md):

| bucket    | holds                        | rule                                |
|-----------|------------------------------|-------------------------------------|
| raw       | the customer's own media     | expire at the org's retention        |
| derived   | extracted audio, ASR replies | expire soon; all of it is rebuildable|
| artifacts | AAF / FCPXML / EDL / OTIO    | keep for a year; it is the delivery  |

And on every bucket, the rule that is not about storage at all:

**Abort incomplete multipart uploads after 7 days.** A failed 200 GB upload
leaves parts that do not appear in the console, are not returned by ListObjects,
and are billed indefinitely. It is the classic silent S3 bill, and the only cure
is this rule.

## Two things to be careful about

**A lifecycle rule is deployed while the previous release is still running**
(ADR-0012). A rule that deletes something the old code still reads is an outage
with no error message on the way in. Expiry days here therefore only ever go
*up* without a release in between; shortening one is a change to make
deliberately, after checking what still reads it.

**Applying is a full replacement.** S3's PutBucketLifecycleConfiguration
replaces the whole configuration rather than merging, so a rule that exists in
the console and not in this file is deleted the moment this runs. That is the
intent — this file is the source of truth — but it means an emergency rule added
by hand will vanish, and anybody adding one has to put it here too.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: The deliverable outlives the media it was cut from. A customer who comes back
#: in six months for the AAF should find it; the rushes are theirs and they have
#: the originals.
ARTIFACT_EXPIRY_DAYS = 365

#: Extracted audio, ASR responses, ingest caches. Every one of them is
#: reproducible from the source, so keeping them past the point where a job
#: might be re-run is paying to store something we can rebuild.
DERIVED_EXPIRY_DAYS = 30

#: The default retention for customer media, matching `orgs.retention_days`.
#: Per-org retention is enforced by the deletion path rather than by a lifecycle
#: rule, because a rule is per bucket and prefix and cannot read a column; this
#: is the backstop for anything that path misses.
RAW_EXPIRY_DAYS = 30

#: Parts of an abandoned multipart upload. Seven days is long enough that a
#: customer who closes their laptop on Friday can resume on Monday, and short
#: enough that a 200 GB abandonment is not a line on the bill all quarter.
ABORT_INCOMPLETE_DAYS = 7


def _abort_rule() -> dict:
    return {
        "ID": "abort-incomplete-multipart-uploads",
        "Status": "Enabled",
        # An empty prefix filter means every object in the bucket. Spelled as
        # `Filter: {"Prefix": ""}` because a rule with no filter at all is
        # rejected by newer API versions.
        "Filter": {"Prefix": ""},
        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": ABORT_INCOMPLETE_DAYS},
    }


def _expiry_rule(name: str, days: int) -> dict:
    return {
        "ID": name,
        "Status": "Enabled",
        "Filter": {"Prefix": "orgs/"},
        "Expiration": {"Days": days},
        # Versioning is on for every bucket, so an expired object becomes a
        # delete marker and the version underneath it keeps costing money until
        # this second rule removes it.
        "NoncurrentVersionExpiration": {"NoncurrentDays": 7},
    }


def rules_for(which: str) -> list[dict]:
    """The lifecycle configuration for one bucket."""
    if which == "raw":
        return [_expiry_rule("expire-raw-media", RAW_EXPIRY_DAYS), _abort_rule()]
    if which == "derived":
        return [_expiry_rule("expire-derived", DERIVED_EXPIRY_DAYS), _abort_rule()]
    if which == "artifacts":
        return [_expiry_rule("expire-artifacts", ARTIFACT_EXPIRY_DAYS), _abort_rule()]
    raise ValueError(f"no lifecycle defined for bucket {which!r}")


def current(client, bucket: str) -> list[dict]:
    try:
        return client.get_bucket_lifecycle_configuration(Bucket=bucket).get("Rules", [])
    except client.exceptions.ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchLifecycleConfiguration", "NoSuchBucket"):
            return []
        raise


def apply(client, bucket: str, which: str) -> bool:
    """Put the rules for `which` onto `bucket`. Returns whether anything changed.

    Idempotent, and cheap to run on every deploy: the comparison is on the rules
    themselves, so a no-op deploy makes no API call that changes anything.
    """
    wanted = rules_for(which)
    if current(client, bucket) == wanted:
        return False
    client.put_bucket_lifecycle_configuration(
        Bucket=bucket, LifecycleConfiguration={"Rules": wanted}
    )
    return True


def apply_all(client, buckets: dict[str, str]) -> dict[str, bool]:
    """`buckets` maps raw/derived/artifacts to actual bucket names."""
    return {name: apply(client, name, which) for which, name in buckets.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply", action="store_true",
        help="write the rules; without it, print what they would be",
    )
    args = parser.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))
    from mishne.config import get_settings
    from mishne.storage import get_client

    settings = get_settings()
    buckets = {
        "raw": settings.s3_bucket_raw,
        "derived": settings.s3_bucket_derived,
        "artifacts": settings.s3_bucket_artifacts,
    }
    if not args.apply:
        print(json.dumps({which: rules_for(which) for which in buckets}, indent=2))
        print(f"\nwould apply to: {json.dumps(buckets)}", file=sys.stderr)
        return 0

    changed = apply_all(get_client(), buckets)
    for bucket, did in changed.items():
        print(f"{bucket}: {'updated' if did else 'already current'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
