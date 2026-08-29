"""Every endpoint returns the same thing from Postgres as it does from fixtures.

That equivalence is what makes `use_mocks` safe to keep: the web app develops
against fixtures, ships against Postgres, and the two are not allowed to drift
into different products. When one of these fails, the seed, the query layer and
`mock.py` disagree — and the fixture is as likely to be wrong as the query.

The Postgres half re-seeds the development database, which is what the
development database is for. It does not touch staging or production: the seed
script refuses to run outside `environment=local`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

pytest.importorskip("sqlalchemy")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

from conftest import mint_session, requires_schema  # noqa: E402

pytestmark = requires_schema

#: Every read path the web app uses.
PATHS = [
    "/v1/projects",
    "/v1/projects/prj_harbour",
    "/v1/projects/prj_harbour/assets",
    "/v1/projects/prj_harbour/jobs",
    "/v1/projects/prj_promo/assets",
    "/v1/assets/ast_5e10",
    "/v1/assets/ast_7c19",
    "/v1/jobs/job_c41a",
    "/v1/jobs/job_2e57",
    "/v1/jobs/job_1d90",
    "/v1/jobs/job_8f23/artifacts",
    "/v1/jobs/job_2e57/transcript",
    "/v1/jobs/job_8f23/transcript",
    "/v1/billing/balance",
    "/v1/billing/ledger",
    "/v1/billing/ledger?project_id=prj_harbour",
]


def _client(clear, *, use_mocks: bool, app_url: str | None = None) -> TestClient:
    import os

    os.environ["ENVIRONMENT"] = "local"
    os.environ["USE_MOCKS"] = "true" if use_mocks else "false"
    if app_url:
        os.environ["APP_DATABASE_URL"] = app_url
    clear()
    from mishne.main import app

    return TestClient(app)


@pytest.fixture
def seeded_token(seeded, owner) -> str:
    """A signed-in user in the seeded organisation.

    Since B4 the org comes from the session and never from a header, so the
    Postgres half of the parity check needs a real one. The fixtures half does
    not: `use_mocks` is a local-only affordance with no tenant to establish.
    """
    from mishne import mock

    return mint_session(owner, mock.ORG.id, mock.USER.id)


@pytest.fixture
def seeded(app_login: str, clear_caches):
    """A database holding exactly the fixtures."""
    import os

    os.environ["ENVIRONMENT"] = "local"
    os.environ["APP_DATABASE_URL"] = app_login
    clear_caches()
    from mishne.db import seed as seed_module

    seed_module.reset()
    seed_module.seed()
    yield
    clear_caches()


@pytest.mark.parametrize("path", PATHS)
def test_postgres_matches_the_fixtures(
    path: str, seeded_token: str, app_login: str, clear_caches
) -> None:
    with _client(clear_caches, use_mocks=True) as mocked:
        expected = mocked.get(path)
    with _client(clear_caches, use_mocks=False, app_url=app_login) as live:
        actual = live.get(path, headers={"Authorization": f"Bearer {seeded_token}"})

    assert expected.status_code == actual.status_code == 200, path
    assert actual.json() == expected.json(), path


def test_another_org_sees_none_of_it(
    seeded, other_tenant: str, app_login: str, clear_caches
) -> None:
    """The API surface of the isolation test: a 404, not somebody else's project.

    Nothing in the router filters by org. The empty result comes from the
    database, which is the only place it can come from and still be true of
    every query written later.

    The caller is a real signed-in owner of a real second organisation, not a
    made-up org id — otherwise this proves that an unknown org sees nothing,
    which is a much weaker statement.
    """
    auth = {"Authorization": f"Bearer {other_tenant}"}
    with _client(clear_caches, use_mocks=False, app_url=app_login) as live:
        assert live.get("/v1/projects", headers=auth).json() == []
        assert live.get("/v1/projects/prj_harbour", headers=auth).status_code == 404
        assert live.get("/v1/jobs/job_2e57/transcript", headers=auth).status_code == 404


def test_an_unauthenticated_request_gets_nothing_at_all(
    seeded, app_login: str, clear_caches
) -> None:
    with _client(clear_caches, use_mocks=False, app_url=app_login) as live:
        assert live.get("/v1/projects").status_code == 401
        assert live.get("/v1/jobs/job_2e57/transcript").status_code == 401


def test_mocks_are_refused_outside_local(clear_caches) -> None:
    """`use_mocks=True` must be unreachable where there is real data.

    An API answering from fixtures in staging reports a balance nobody has and a
    job nobody ran, convincingly. Failing to boot is the cheaper failure.
    """
    import os

    from pydantic import ValidationError

    from mishne.config import Settings

    os.environ["ENVIRONMENT"] = "local"
    clear_caches()
    with pytest.raises(ValidationError):
        Settings(environment="staging", use_mocks=True)
    # And the combination that is fine.
    # A staging Settings also needs a KMS key now — customer media is never
    # unencrypted at rest outside a developer's machine (B2).
    ok = Settings(
        environment="staging",
        use_mocks=False,
        s3_kms_key_id="alias/mishne-staging",
        # https, because the session cookie is the credential for every request
        # and sending it in the clear is not a state this system may reach (B4).
        app_origin="https://staging.mishne.ai",
    )
    assert ok.use_mocks is False
