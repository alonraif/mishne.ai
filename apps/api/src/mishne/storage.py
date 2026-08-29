"""Object storage: the three buckets, the key scheme, and presigned multipart.

Media never transits the API (ADR in docs/architecture/03). The browser is
handed presigned URLs and talks to S3 directly, so a 60 GB ProRes master never
touches an application server. Everything in this module is therefore about
*minting credentials for someone else to use*, which is why two rules run
through it:

**A presigned URL is a credential.** It is a signed, time-limited grant to write
to (or read) one specific object. Never log one, never put one in an error
message, never store one. `mishne.logging` blocks the `s3_key` key; the URLs
themselves must simply never be handed to a logger. TTL is
`presign_ttl_seconds` — 900 by default, which is long enough for a part and
short enough that a leaked URL is a small problem.

**Keys are opaque.** A key is an id, not a filename. The name the customer
uploaded lives in `assets.filename`, where it is a column that can be redacted,
not a substring of a URL that turns up in an access log. The key scheme is:

    orgs/{org_id}/projects/{project_id}/assets/{asset_id}/source
    orgs/{org_id}/projects/{project_id}/assets/{asset_id}/derived/{name}
    orgs/{org_id}/jobs/{job_id}/artifacts/{name}

It leads with the tenant on purpose. Every lifecycle rule, every bulk delete for
a retention request, and every "what does this customer store" question is then
a prefix query, and an IAM policy scoped to one org is a prefix condition rather
than a tag scan.

## Which bucket

| bucket      | holds                                   | why separate            |
|-------------|-----------------------------------------|-------------------------|
| raw         | what the customer uploaded              | their IP; expensive     |
| derived     | extracted audio, ingest caches          | reproducible; disposable|
| artifacts   | AAF / FCPXML / EDL / OTIO / transcript  | the deliverable         |

Three buckets rather than one because the lifecycle differs by an order of
magnitude in both directions, and a lifecycle rule is per-bucket-per-prefix.
Getting this wrong in the other direction — one bucket, prefix rules — works
until somebody writes a rule that deletes an artifact because it sat under a
prefix that looked like a cache.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import IO, Literal

import boto3
from botocore.client import Config

from .config import Settings, get_settings

Bucket = Literal["raw", "derived", "artifacts"]

#: S3's minimum part size for every part except the last. A smaller part size is
#: rejected at CompleteMultipartUpload — after the whole file has been sent.
MIN_PART_SIZE = 5 * 1024 * 1024

#: The part size we actually ask for. 64 MiB keeps the part count for a 60 GB
#: upload at ~960, comfortably inside S3's 10,000-part ceiling, while staying
#: small enough that re-sending one failed part after a dropped connection costs
#: seconds rather than minutes. Both of those matter: the ceiling is a hard
#: failure at the end of a long upload, and the retry cost is what "resumable"
#: means to a user on hotel wifi.
DEFAULT_PART_SIZE = 64 * 1024 * 1024

#: S3's hard limit on parts in one multipart upload.
MAX_PARTS = 10_000

#: Largest single object S3 will accept. Also the point at which we should be
#: having a different conversation with the customer.
MAX_OBJECT_BYTES = 5 * 1024**4  # 5 TiB


@dataclass(frozen=True)
class ObjectRef:
    """Where an object is. Not how to get it — that is `Workspace`."""

    bucket: str
    key: str

    def __str__(self) -> str:  # pragma: no cover - debugging affordance
        return f"s3://{self.bucket}/{self.key}"


@dataclass(frozen=True)
class PartUrl:
    part_number: int
    url: str
    #: Byte range of the source file this part covers. The browser needs it to
    #: slice the File, and it is what makes a resumed upload able to re-send
    #: exactly one part.
    offset: int
    length: int


@dataclass(frozen=True)
class MultipartUpload:
    ref: ObjectRef
    upload_id: str
    part_size: int
    parts: list[PartUrl]


def part_count(total_bytes: int, part_size: int = DEFAULT_PART_SIZE) -> int:
    """How many parts a file of this size needs.

    Zero bytes is one part, not zero: S3 will not complete an upload with no
    parts, and an empty file is a real thing a user can select by accident. It
    fails later, on probe, with a message about having no audio — which is a
    better error than a signature mismatch.
    """
    if total_bytes <= 0:
        return 1
    return max(1, -(-total_bytes // part_size))


def choose_part_size(total_bytes: int) -> int:
    """A part size that keeps the part count under S3's ceiling.

    At the default 64 MiB the ceiling binds at 640 GB. Beyond that the part size
    grows rather than the count, because exceeding 10,000 parts is not a
    degraded upload, it is a failure at CompleteMultipartUpload with the whole
    file already sent.
    """
    size = DEFAULT_PART_SIZE
    while part_count(total_bytes, size) > MAX_PARTS:
        size *= 2
    return size


# ────────────────────────────────────────────────────────────────── key scheme


def source_key(org_id: str, project_id: str, asset_id: str) -> str:
    """Where an upload lands. Deterministic, so a retried upload overwrites
    itself rather than leaving an orphan to pay for."""
    return f"orgs/{org_id}/projects/{project_id}/assets/{asset_id}/source"


def derived_key(org_id: str, project_id: str, asset_id: str, name: str) -> str:
    return f"orgs/{org_id}/projects/{project_id}/assets/{asset_id}/derived/{name}"


def artifact_key(org_id: str, job_id: str, name: str) -> str:
    return f"orgs/{org_id}/jobs/{job_id}/artifacts/{name}"


def parse_source_key(key: str) -> tuple[str, str, str] | None:
    """(org, project, asset) from a source key, or None if it is not one.

    The inverse of `source_key`, and the reason an S3 event notification needs
    no database lookup to know what arrived. Strict on purpose: anything that
    does not match the scheme exactly returns None rather than a best guess,
    because the caller acts on the result by writing to that org's rows.
    """
    parts = key.split("/")
    if len(parts) != 7:
        return None
    orgs, org_id, projects, project_id, assets, asset_id, leaf = parts
    if (orgs, projects, assets, leaf) != ("orgs", "projects", "assets", "source"):
        return None
    if not (org_id and project_id and asset_id):
        return None
    return org_id, project_id, asset_id


def org_prefix(org_id: str) -> str:
    """Everything one tenant stores in a bucket. The unit of a deletion request."""
    return f"orgs/{org_id}/"


def content_id(digest: str) -> str:
    """The asset id for a piece of content.

    Content-addressed, so the same rushes uploaded twice are one asset and one
    transcription. `pipeline.project.asset_id_for` used filename plus size and
    said in a comment that a content hash was the right answer, deferred because
    it costs a full read of a very large file. With real storage the read is no
    longer extra: the browser has to read every byte to upload them, so it
    computes the digest on the way past and sends it with the request.

    Truncated to 24 hex characters — 96 bits. Birthday collision at ~2^48
    objects, which is not a number this system will reach, and a full 64-char
    id makes every work-directory path unreadable.
    """
    if len(digest) < 24 or not all(c in "0123456789abcdef" for c in digest.lower()):
        raise ValueError("content id wants a hex sha-256 digest")
    return f"a_{digest.lower()[:24]}"


def sha256_file(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    """Digest a file without reading it into memory.

    An AAF with embedded essence is routinely tens of gigabytes; `read()` on one
    is an OOM kill, not a slow function.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


# ───────────────────────────────────────────────────────────────────── client


@lru_cache
def get_client(endpoint_url: str | None = None):
    """The S3 client.

    `signature_version="s3v4"` is explicit because presigned URLs generated with
    SigV2 are rejected by buckets created after 2018 and by every region that
    has only ever supported SigV4 — and the failure is a 400 from S3 at upload
    time, long after the code that chose the signature has returned.

    `addressing_style="virtual"` is the modern default and the one MinIO also
    accepts when given a domain; the local-development override sets `path`,
    because MinIO on `localhost:9000` cannot do virtual-host addressing.
    """
    settings = get_settings()
    endpoint = endpoint_url if endpoint_url is not None else settings.s3_endpoint_url
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        endpoint_url=endpoint or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if endpoint else "virtual"},
            retries={"max_attempts": 5, "mode": "adaptive"},
        ),
    )


def bucket_for(which: Bucket, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    return {
        "raw": s.s3_bucket_raw,
        "derived": s.s3_bucket_derived,
        "artifacts": s.s3_bucket_artifacts,
    }[which]


class Storage:
    """The object operations this system performs, and no others.

    Deliberately not a thin wrapper over boto3: every method here is a thing the
    product does, so the places that mint credentials are countable. A component
    that needs an operation not on this list is doing something that deserves a
    second look.
    """

    def __init__(self, settings: Settings | None = None, client=None) -> None:
        self.settings = settings or get_settings()
        self.client = client or get_client()

    # ── multipart upload ───────────────────────────────────────────────────

    def initiate_multipart(
        self, ref: ObjectRef, *, content_type: str = "application/octet-stream"
    ) -> str:
        resp = self.client.create_multipart_upload(
            Bucket=ref.bucket,
            Key=ref.key,
            ContentType=content_type,
            # Server-side encryption with the bucket's KMS key. Set per request
            # as well as by bucket policy: a bucket default can be changed by
            # somebody who does not know it is load-bearing, and customer media
            # is under embargo often enough that unencrypted-at-rest is not a
            # state this system should be able to reach.
            **self._encryption(),
        )
        return resp["UploadId"]

    def part_urls(
        self, ref: ObjectRef, upload_id: str, total_bytes: int, part_size: int
    ) -> list[PartUrl]:
        """One presigned PUT per part.

        All of them at once, rather than one per request: a 60 GB upload is ~960
        parts, and a round trip to the API between each is both slow and a
        source of half-uploaded state when the page is closed. The TTL applies
        from now, so a very long upload will outlive them — the client re-asks
        for the remaining parts, which is exactly the resume path.
        """
        urls: list[PartUrl] = []
        for i in range(part_count(total_bytes, part_size)):
            offset = i * part_size
            urls.append(
                PartUrl(
                    part_number=i + 1,  # S3 part numbers are 1-based.
                    url=self.client.generate_presigned_url(
                        "upload_part",
                        Params={
                            "Bucket": ref.bucket,
                            "Key": ref.key,
                            "UploadId": upload_id,
                            "PartNumber": i + 1,
                        },
                        ExpiresIn=self.settings.presign_ttl_seconds,
                    ),
                    offset=offset,
                    length=min(part_size, max(0, total_bytes - offset)),
                )
            )
        return urls

    def complete_multipart(
        self, ref: ObjectRef, upload_id: str, parts: list[tuple[int, str]]
    ) -> None:
        """Assemble the parts into an object.

        Takes (part number, etag) pairs rather than etags in order. Parts are
        uploaded concurrently and retried out of order, so "the third etag I
        collected" and "part three" are not the same thing, and S3 will happily
        complete an upload whose bytes are in the wrong places. Sorted here
        because CompleteMultipartUpload requires ascending part numbers and
        rejects the whole request if they are not — after every byte has been
        sent.
        """
        self.client.complete_multipart_upload(
            Bucket=ref.bucket,
            Key=ref.key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": number, "ETag": tag}
                    for number, tag in sorted(parts)
                ]
            },
        )

    def list_parts(self, ref: ObjectRef, upload_id: str) -> list[tuple[int, str, int]]:
        """The parts S3 already holds for an in-flight upload.

        This is what makes a resume survive a closed laptop rather than only a
        dropped connection. The browser cannot ask S3 directly — a presigned URL
        grants one operation on one object — so the API asks on its behalf, and
        the client re-sends only what is missing.
        """
        parts: list[tuple[int, str, int]] = []
        paginator = self.client.get_paginator("list_parts")
        for page in paginator.paginate(
            Bucket=ref.bucket, Key=ref.key, UploadId=upload_id
        ):
            for part in page.get("Parts", []):
                parts.append((part["PartNumber"], part["ETag"], part["Size"]))
        return sorted(parts)

    def abort_multipart(self, ref: ObjectRef, upload_id: str) -> None:
        """Stop paying for an abandoned upload.

        Called on an explicit cancel. The lifecycle rule in
        `infra/s3_lifecycle.py` is the backstop for the ones nobody cancels —
        parts of an abandoned multipart upload are invisible in the console and
        billed indefinitely, which is the classic silent S3 bill.
        """
        self.client.abort_multipart_upload(
            Bucket=ref.bucket, Key=ref.key, UploadId=upload_id
        )

    # ── reading and writing ────────────────────────────────────────────────

    def presigned_get(self, ref: ObjectRef, *, filename: str | None = None) -> str:
        """A time-limited download URL.

        `filename` sets Content-Disposition so the browser saves
        `interview_rough_cut.aaf` rather than the opaque key. That is the only
        place a customer's filename enters a URL, it is response metadata rather
        than the path, and it is why artifact downloads are audit-logged.
        """
        params: dict[str, str] = {"Bucket": ref.bucket, "Key": ref.key}
        if filename:
            params["ResponseContentDisposition"] = (
                f'attachment; filename="{filename}"'
            )
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=self.settings.presign_ttl_seconds
        )

    def head(self, ref: ObjectRef) -> dict | None:
        """Size, etag and metadata, or None if the object is not there."""
        try:
            return self.client.head_object(Bucket=ref.bucket, Key=ref.key)
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    def exists(self, ref: ObjectRef) -> bool:
        return self.head(ref) is not None

    def download(self, ref: ObjectRef, dest: Path) -> Path:
        """Stream an object to a real file on local disk.

        This is the whole reason `Workspace` exists — see workspace.py for why
        the pipeline needs a path and not a stream.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(ref.bucket, ref.key, str(dest))
        return dest

    def upload(self, src: Path, ref: ObjectRef) -> None:
        self.client.upload_file(
            str(src), ref.bucket, ref.key, ExtraArgs=self._encryption() or None
        )

    def put_bytes(self, data: bytes | IO[bytes], ref: ObjectRef) -> None:
        self.client.put_object(
            Bucket=ref.bucket, Key=ref.key, Body=data, **self._encryption()
        )

    def get_bytes(self, ref: ObjectRef) -> bytes | None:
        try:
            return self.client.get_object(Bucket=ref.bucket, Key=ref.key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        except self.client.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return None
            raise

    def delete_prefix(self, bucket: str, prefix: str) -> int:
        """Delete everything under a prefix. The mechanism behind a retention
        request, which is why it takes a prefix and not a wildcard."""
        paginator = self.client.get_paginator("list_objects_v2")
        deleted = 0
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not keys:
                continue
            self.client.delete_objects(Bucket=bucket, Delete={"Objects": keys})
            deleted += len(keys)
        return deleted

    # ── internals ──────────────────────────────────────────────────────────

    def _encryption(self) -> dict:
        if self.settings.s3_kms_key_id:
            return {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": self.settings.s3_kms_key_id,
            }
        # No KMS key configured means local development against MinIO or moto,
        # neither of which has one. In staging and production the key is set by
        # environment and `Settings` is where that is checked.
        return {}


@lru_cache
def get_storage() -> Storage:
    return Storage()
