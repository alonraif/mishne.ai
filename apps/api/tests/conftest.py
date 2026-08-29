"""Shared fixtures for the database-backed tests.

Everything here degrades to a skip when there is no Postgres, so the pipeline
tests still run on a machine that has never started docker-compose:

    docker compose -f infra/docker-compose.yml up -d
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

APP_ROLE = "mishne_app"
TEST_LOGIN = "mishne_test_app"
TEST_PASSWORD = "mishne_test_app"

# The database tooling is a declared dependency, but a venv built before B1 —
# or one built from the pipeline list alone — does not have it. Degrade to a
# skip rather than breaking collection for the pipeline tests, which need none
# of this.
try:
    import sqlalchemy as sa

    from mishne.config import get_settings
    from mishne.db.base import get_engine, get_sessionmaker, normalise_url

    _MISSING = ""
except ImportError as exc:  # pragma: no cover - environment, not logic
    sa = None  # type: ignore[assignment]
    _MISSING = f"{exc} — run ./setup.sh"


def _owner_url() -> str:
    return normalise_url(get_settings().database_url)


def _probe(statement: str | None = None) -> bool:
    """Can we connect, and optionally: does `statement` come back true?"""
    if _MISSING:
        return False
    engine = None
    try:
        engine = sa.create_engine(_owner_url(), connect_args={"connect_timeout": 2})
        with engine.connect() as conn:
            return True if statement is None else bool(conn.execute(sa.text(statement)).scalar())
    except Exception:
        return False
    finally:
        if engine is not None:
            engine.dispose()


#: A reachable server. Enough for the migration tests, which build their own
#: scratch database and are the one thing that must run before any schema exists.
requires_postgres = pytest.mark.skipif(
    not _probe(),
    reason=(
        _MISSING
        or "no Postgres — start it with docker compose -f infra/docker-compose.yml up -d"
    ),
)

#: A server with the schema already on it. Everything that queries real tables
#: needs this, and `setup.sh` runs pytest before anyone has had the chance to
#: migrate — so these skip rather than fail on a fresh clone.
requires_schema = pytest.mark.skipif(
    not _probe("SELECT to_regclass('public.orgs') IS NOT NULL"),
    reason=(
        _MISSING
        or "no migrated schema — docker compose up -d, then alembic upgrade head"
    ),
)


@pytest.fixture(scope="session")
def owner():
    """The migration connection. Superuser locally, so it bypasses every policy."""
    engine = sa.create_engine(_owner_url())
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def app_login(owner) -> str:
    """A login role that is a member of `mishne_app` — and nothing more.

    Created here rather than assumed, so the isolation test is self-contained.
    The two `ALTER`s are the point of the fixture: a superuser or a role with
    BYPASSRLS reads every tenant's rows without raising anything, and a test
    that connects as one passes while proving nothing at all.
    """
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(
            sa.text(
                f"""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{TEST_LOGIN}') THEN
                        CREATE ROLE {TEST_LOGIN} LOGIN PASSWORD '{TEST_PASSWORD}';
                    END IF;
                END
                $$;
                """
            )
        )
        conn.execute(sa.text(f"GRANT {APP_ROLE} TO {TEST_LOGIN}"))
        conn.execute(sa.text(f"ALTER ROLE {TEST_LOGIN} NOSUPERUSER NOBYPASSRLS"))

    url = sa.engine.make_url(_owner_url()).set(
        username=TEST_LOGIN, password=TEST_PASSWORD
    )
    return url.render_as_string(hide_password=False)


@pytest.fixture
def clear_caches():
    """Settings and engines are memoised; a test that changes either must reset both."""
    def _clear() -> None:
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()

    _clear()
    yield _clear
    _clear()


# ────────────────────────────────────────────── the upload and probe fixtures

#: A tenant of the tests' own, so nothing here depends on — or disturbs — the
#: seeded development data.
ORG = "org_test_upload"
PROJECT = "prj_test_upload"
#: An owner and a viewer, so the role gates have something to refuse.
OWNER_USER = "usr_test_owner"
VIEWER_USER = "usr_test_viewer"
BUCKETS = ("test-raw", "test-derived", "test-artifacts")

#: The part size these tests run at. The production default is 64 MiB, which
#: would mean pushing 128 MiB through the suite to see a second part at all;
#: S3's floor is 5 MiB, so the fixture patches the default down to it and an
#: 11 MiB blob exercises a genuine three-part upload.
PART_SIZE = 5 * 1024 * 1024

REGION = "eu-west-1"

# Guarded rather than `importorskip`: this is a conftest, and an importorskip at
# module level would skip every test in the directory, including the pipeline
# ones that need none of it.
try:
    import boto3
    import moto
    from fastapi.testclient import TestClient

    _S3_MISSING = ""
except ImportError as exc:  # pragma: no cover - environment, not logic
    boto3 = moto = TestClient = None  # type: ignore[assignment]
    _S3_MISSING = f"{exc} — run ./setup.sh"


def digest(blob: bytes) -> str:
    import hashlib

    return hashlib.sha256(blob).hexdigest()


def mint_session(owner_engine, org_id: str, user_id: str, *, expired: bool = False) -> str:
    """A signed-in browser, made directly.

    Inserted through the owner connection rather than by calling `/auth/login`,
    because most tests are not testing sign-in — they need a caller that exists.
    The token still goes through the real resolution path on every request,
    including the policy escape that reads it.
    """
    import hashlib
    import secrets
    from datetime import datetime, timedelta, timezone

    token = secrets.token_urlsafe(24)
    expires = datetime.now(timezone.utc) + (
        timedelta(seconds=-1) if expired else timedelta(days=1)
    )
    with owner_engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO sessions (id, org_id, user_id, token_hash, expires_at) "
                "VALUES (:i, :o, :u, :h, :e)"
            ),
            {
                "i": f"ses_{secrets.token_hex(6)}",
                "o": org_id,
                "u": user_id,
                "h": hashlib.sha256(token.encode()).hexdigest(),
                "e": expires,
            },
        )
    return token


def create_asset(http, blob: bytes, filename: str = "A002.mxf") -> dict:
    resp = http.post(
        f"/v1/projects/{PROJECT}/assets",
        json={
            "filename": filename,
            "bytes": len(blob),
            "checksum": digest(blob),
            "ingest_mode": "full_media",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def send_parts(client, key: str, upload_id: str, blob: bytes, part_size: int):
    """Stand in for the browser.

    `part_size` is the one the SERVER handed back, never a number the test
    picked: a client that slices to its own idea of a part size uploads bytes
    that do not line up with the layout the completion is checked against, and
    S3 assembles them without complaint.

    moto intercepts botocore rather than raw HTTP, so the parts go through the
    SDK; the presigned URLs themselves are asserted on in test_storage.
    """
    parts = []
    for i in range(0, max(1, -(-len(blob) // part_size))):
        chunk = blob[i * part_size : (i + 1) * part_size]
        resp = client.upload_part(
            Bucket="test-raw", Key=key, UploadId=upload_id, PartNumber=i + 1, Body=chunk
        )
        parts.append({"part_number": i + 1, "etag": resp["ETag"]})
    return parts


def asset_row(owner, asset_id: str):
    with owner.begin() as conn:
        return conn.execute(
            sa.text("SELECT * FROM assets WHERE id = :a"), {"a": asset_id}
        ).first()


@pytest.fixture
def tenant(owner):
    """An org and a project of this test's own, removed afterwards."""
    with owner.begin() as conn:
        # `projects` carries org_id but no foreign key to `orgs` — org_id is on
        # every table by design, not by reference — so nothing cascades from an
        # org and the teardown has to name each table. Assets do cascade from
        # their project.
        conn.execute(sa.text("DELETE FROM projects WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM users WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM orgs WHERE id = :o"), {"o": ORG})
        conn.execute(
            sa.text(
                "INSERT INTO orgs (id, name, tier, retention_days) "
                "VALUES (:o, 'Upload test', 'pro', 30)"
            ),
            {"o": ORG},
        )
        conn.execute(
            sa.text("INSERT INTO projects (id, org_id, name) VALUES (:p, :o, 'Upload test')"),
            {"p": PROJECT, "o": ORG},
        )
        conn.execute(
            sa.text(
                "INSERT INTO users (id, org_id, email, name, role, auth_provider) VALUES "
                "(:owner, :o, :owner_email, 'Test Owner', 'owner', 'local'), "
                "(:viewer, :o, :viewer_email, 'Test Viewer', 'viewer', 'local')"
            ),
            {
                "owner": OWNER_USER,
                "viewer": VIEWER_USER,
                "o": ORG,
                "owner_email": f"{OWNER_USER}@example.test",
                "viewer_email": f"{VIEWER_USER}@example.test",
            },
        )
    yield
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM projects WHERE org_id = :o"), {"o": ORG})
        # sessions and credentials cascade from users; users do not cascade from
        # the org, because org_id is a column and not a reference.
        conn.execute(sa.text("DELETE FROM users WHERE org_id = :o"), {"o": ORG})
        conn.execute(sa.text("DELETE FROM orgs WHERE id = :o"), {"o": ORG})


@pytest.fixture
def api(tenant, owner, app_login, monkeypatch, clear_caches):
    """The app, talking to Postgres as a role RLS actually applies to, and to moto."""
    if _S3_MISSING:
        pytest.skip(_S3_MISSING)
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("USE_MOCKS", "false")
    monkeypatch.setenv("APP_DATABASE_URL", app_login)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("S3_BUCKET_RAW", "test-raw")
    monkeypatch.setenv("S3_BUCKET_DERIVED", "test-derived")
    monkeypatch.setenv("S3_BUCKET_ARTIFACTS", "test-artifacts")
    monkeypatch.setenv("AWS_REGION", REGION)
    clear_caches()

    from mishne import storage

    storage.get_client.cache_clear()
    storage.get_storage.cache_clear()
    monkeypatch.setattr(storage, "DEFAULT_PART_SIZE", PART_SIZE)

    with moto.mock_aws():
        client = boto3.client("s3", region_name=REGION)
        for bucket in BUCKETS:
            client.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": REGION}
            )
        from mishne.main import app

        with TestClient(app) as http:
            # A real session, resolved through the real policy escape on every
            # request. The bearer form rather than a cookie only because a test
            # client has no cookie jar worth maintaining; the code path after
            # the token is read is identical.
            http.headers.update(
                {"Authorization": f"Bearer {mint_session(owner, ORG, OWNER_USER)}"}
            )
            yield http, client
    storage.get_client.cache_clear()
    storage.get_storage.cache_clear()


@pytest.fixture
def viewer_token(tenant, owner) -> str:
    """A signed-in viewer: reads and downloads, uploads nothing."""
    return mint_session(owner, ORG, VIEWER_USER)


@pytest.fixture
def other_tenant(owner):
    """A second organisation with its own signed-in owner.

    Cross-tenant tests need a caller that genuinely exists somewhere else: a
    made-up org id proves only that the org is unknown, not that the isolation
    holds against a real session.
    """
    other_org = "org_test_other"
    other_user = "usr_test_other"
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE org_id = :o"), {"o": other_org})
        conn.execute(sa.text("DELETE FROM orgs WHERE id = :o"), {"o": other_org})
        conn.execute(
            sa.text(
                "INSERT INTO orgs (id, name, tier, retention_days) "
                "VALUES (:o, 'Somebody else', 'starter', 30)"
            ),
            {"o": other_org},
        )
        conn.execute(
            sa.text(
                "INSERT INTO users (id, org_id, email, name, role, auth_provider) "
                "VALUES (:u, :o, :e, 'Other Owner', 'owner', 'local')"
            ),
            {"u": other_user, "o": other_org, "e": f"{other_user}@example.test"},
        )
    yield mint_session(owner, other_org, other_user)
    with owner.begin() as conn:
        conn.execute(sa.text("DELETE FROM users WHERE org_id = :o"), {"o": other_org})
        conn.execute(sa.text("DELETE FROM orgs WHERE id = :o"), {"o": other_org})
