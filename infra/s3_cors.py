"""Bucket CORS: what the browser is allowed to do directly against S3.

Uploads go browser → S3, which makes the browser a cross-origin client of the
bucket. Without this configuration every part PUT fails at the preflight, and
the failure is a console message rather than an HTTP status the app can act on.

    python infra/s3_cors.py --apply --origin https://app.mishne.ai
    python infra/s3_cors.py                       # print what it would set

## The header that is easy to miss

**`ExposeHeaders: ["ETag"]`.** CompleteMultipartUpload needs each part's ETag,
S3 returns it as a response header on the PUT, and a cross-origin response
exposes *no* headers to script unless the bucket says so. Leave it out and every
part uploads perfectly, `xhr.getResponseHeader("ETag")` returns null, and the
upload fails at the last step with the whole file already sent.

`Content-Length` and `x-amz-*` are not needed by the client and are not exposed:
a CORS policy is an access grant, and the list should be the shortest one that
works.

## Origins are named, never `*`

A wildcard would let any page on the internet drive an upload with a presigned
URL it obtained some other way. The presign is the credential and the CORS
policy is not a security boundary on its own — but widening it for convenience
removes a check that costs nothing to keep.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import s3_local  # noqa: E402

#: The raw bucket takes multipart PUTs from the app. The other two are read
#: through presigned GETs, which a download link follows rather than script.
UPLOAD_METHODS = ["PUT", "POST", "HEAD", "GET"]
DOWNLOAD_METHODS = ["GET", "HEAD"]

#: How long a browser may cache the preflight. An hour keeps a 960-part upload
#: from preflighting 960 times.
MAX_AGE_SECONDS = 3600


def rules_for(which: str, origins: list[str]) -> list[dict]:
    methods = UPLOAD_METHODS if which == "raw" else DOWNLOAD_METHODS
    return [
        {
            "AllowedOrigins": origins,
            "AllowedMethods": methods,
            # The browser sends Content-Type and the range headers it likes;
            # allowing the request headers it actually uses rather than "*"
            # keeps the grant readable.
            "AllowedHeaders": ["content-type", "content-md5", "x-amz-*", "authorization"],
            # Without this the ETag is invisible to script and the upload cannot
            # be completed. See the module docstring.
            "ExposeHeaders": ["ETag"],
            "MaxAgeSeconds": MAX_AGE_SECONDS,
        }
    ]


def current(client, bucket: str) -> list[dict]:
    try:
        return client.get_bucket_cors(Bucket=bucket).get("CORSRules", [])
    except client.exceptions.ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in (
            "NoSuchCORSConfiguration",
            "NoSuchBucket",
        ):
            return []
        raise


def apply(client, bucket: str, which: str, origins: list[str],
          endpoint_url: str = "") -> bool:
    """Idempotent, like the lifecycle rules: returns whether anything changed.

    `endpoint_url` is passed rather than read so this stays a pure function of
    its arguments — and it is only used to decide whether a `NotImplemented`
    from the endpoint is MinIO being MinIO or S3 being misconfigured. See
    `s3_local.py`.
    """
    wanted = rules_for(which, origins)
    try:
        if current(client, bucket) == wanted:
            return False
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": wanted})
    except client.exceptions.ClientError as exc:
        if s3_local.tolerable_locally(exc, endpoint_url):
            s3_local.note_skipped("bucket CORS", bucket)
            return False
        raise
    return True


def apply_all(client, buckets: dict[str, str], origins: list[str],
              endpoint_url: str = "") -> dict[str, bool]:
    return {name: apply(client, name, which, origins, endpoint_url)
            for which, name in buckets.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--origin", action="append", default=[],
        help="an allowed origin; repeatable. Defaults to the local dev app.",
    )
    args = parser.parse_args(argv)
    origins = args.origin or ["http://localhost:3000"]

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))
    from mishne.config import get_settings, load_env_file
    from mishne.storage import get_client

    # boto3 reads credentials from the environment, and `Settings` reading .env
    # does not put them there — so without this, a script pointed at MinIO by
    # S3_ENDPOINT_URL still tries to authenticate as whatever AWS profile the
    # machine happens to have. See `config.load_env_file`.
    load_env_file(Path(__file__).resolve().parent.parent / "apps" / "api" / ".env")

    settings = get_settings()
    buckets = {
        "raw": settings.s3_bucket_raw,
        "derived": settings.s3_bucket_derived,
        "artifacts": settings.s3_bucket_artifacts,
    }
    if not args.apply:
        print(json.dumps({w: rules_for(w, origins) for w in buckets}, indent=2))
        return 0

    for bucket, changed in apply_all(
        get_client(), buckets, origins, settings.s3_endpoint_url
    ).items():
        print(f"{bucket}: {'updated' if changed else 'already current'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
