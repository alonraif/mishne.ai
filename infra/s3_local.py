"""What MinIO does not implement, and why that is not a failure locally.

`s3_cors.py` and `s3_lifecycle.py` configure real buckets in staging and
production, where every call they make is supported and a failure means the
media buckets are wrong. Pointed at MinIO on a laptop, some of those calls come
back `NotImplemented` — MinIO is S3-compatible, not S3, and bucket CORS in
particular is something it answers permissively by default rather than
something it lets you configure.

Crashing there is wrong, and so is swallowing the error. The rule is narrow
enough to state exactly: an unimplemented operation is tolerated **only** when
the client is pointed at a non-AWS endpoint, which `Settings` already refuses to
allow outside `environment=local` (ADR-0012). Against real S3 the same error
still stops the script, because there it means the buckets a customer's footage
lives in are not configured the way this file says they are.
"""

from __future__ import annotations

#: MinIO's answer to an S3 API it does not have. The message is
#: "A header you provided implies functionality that is not implemented",
#: which is not obviously about the operation at all.
UNSUPPORTED = ("NotImplemented", "MethodNotAllowed", "NotSupported")


def tolerable_locally(exc, endpoint_url: str) -> bool:
    """Whether this error is MinIO being MinIO rather than something wrong."""
    if not endpoint_url:
        return False
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in UNSUPPORTED


def note_skipped(what: str, bucket: str) -> None:
    print(
        f"{bucket}: {what} not supported by this endpoint — skipped.\n"
        f"    MinIO allows browser requests from any origin by default, which "
        f"is fine locally and is not what production does; the same command "
        f"against S3 applies the real rules."
    )
