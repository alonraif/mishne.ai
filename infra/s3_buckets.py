"""Create the three local buckets. Local only.

    python infra/s3_buckets.py

The lifecycle and CORS scripts beside this one configure buckets; nothing
created them, because in staging and production they are Terraform's and
nobody's laptop should be able to make one by accident. On a developer's
machine there is no Terraform and MinIO starts empty, so the first upload fails
against a bucket that does not exist — with an S3 error a browser reports as a
CORS failure, which sends the next hour in the wrong direction entirely.

Refuses to run outside `environment=local` for the same reason
`Settings._mocks_never_where_there_is_real_data` does: the three buckets are
where customer media lives, and a script that creates them is a script that can
create the wrong ones.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "apps" / "api" / "src"))

from mishne.config import get_settings, load_env_file  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    load_env_file(Path(__file__).resolve().parent.parent / "apps" / "api" / ".env")
    settings = get_settings()
    if settings.environment != "local":
        print(f"refusing to create buckets in environment={settings.environment!r}; "
              "outside local these belong to Terraform")
        return 1
    if not settings.s3_endpoint_url:
        print("S3_ENDPOINT_URL is not set, so this would create buckets in real "
              "AWS. Set it to MinIO (http://localhost:9000) first.")
        return 1

    from mishne.storage import get_client

    client = get_client()
    existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    for bucket in (settings.s3_bucket_raw, settings.s3_bucket_derived,
                   settings.s3_bucket_artifacts):
        if bucket in existing:
            print(f"{bucket}: already there")
            continue
        # No LocationConstraint: MinIO takes the default region and rejects a
        # constraint naming one it was not started with.
        client.create_bucket(Bucket=bucket)
        print(f"{bucket}: created")

    # Versioning, on every bucket, because the deployed ones have it and two
    # things here depend on it: the lifecycle rules expire noncurrent versions
    # (`s3_lifecycle._expiry_rule`), which is not a meaningful thing to ask of
    # an unversioned bucket and which some endpoints refuse outright; and a
    # delete against a versioned bucket writes a marker rather than removing
    # the object, which is different behaviour for the retention path to be
    # tested against.
    #
    # Idempotent: enabling it on a bucket that already has it is a no-op.
    for bucket in (settings.s3_bucket_raw, settings.s3_bucket_derived,
                   settings.s3_bucket_artifacts):
        client.put_bucket_versioning(
            Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
        )
    print("versioning: enabled on all three")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
