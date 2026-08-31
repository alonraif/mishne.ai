"""Creating a project.

Against a real database, because the two things worth asserting are both the
database's: that the row lands in the caller's org and nowhere else, and that
the three numbers on the response are aggregates rather than stored counters.

Skips itself when there is no migrated schema — see conftest.
"""

from __future__ import annotations

import pytest

sa = pytest.importorskip("sqlalchemy")

from conftest import ORG, requires_schema  # noqa: E402

pytestmark = requires_schema


def _project_row(owner, project_id: str):
    with owner.begin() as conn:
        return conn.execute(
            sa.text("SELECT * FROM projects WHERE id = :p"), {"p": project_id}
        ).first()


def test_a_new_project_is_created_empty_and_readable(api, owner):
    http, _ = api

    created = http.post("/v1/projects", json={"name": "Harbour Lights — Ep. 4"})
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["name"] == "Harbour Lights — Ep. 4"
    assert body["org_id"] == ORG
    # The three numbers are computed from rows that do not exist yet, which is
    # the only reason they are zero.
    assert (body["asset_count"], body["job_count"], body["credits_used"]) == (0, 0, 0)

    row = _project_row(owner, body["id"])
    assert row.org_id == ORG
    assert row.archived_at is None
    # Attributed to the session's user, never to anything in the request body.
    assert row.created_by == "usr_test_owner"

    fetched = http.get(f"/v1/projects/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    assert body["id"] in [p["id"] for p in http.get("/v1/projects").json()]


def test_the_name_is_trimmed_and_an_empty_one_is_refused(api):
    http, _ = api

    created = http.post("/v1/projects", json={"name": "  Nordic\n  Summit  "})
    assert created.status_code == 201, created.text
    assert created.json()["name"] == "Nordic Summit"

    for blank in ("", "   ", "\n\t"):
        refused = http.post("/v1/projects", json={"name": blank})
        assert refused.status_code == 422, blank

    too_long = http.post("/v1/projects", json={"name": "x" * 121})
    assert too_long.status_code == 422


def test_two_projects_may_share_a_name(api):
    http, _ = api

    first = http.post("/v1/projects", json={"name": "Day 1"})
    second = http.post("/v1/projects", json={"name": "Day 1"})
    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] != second.json()["id"]


def test_creating_is_written_to_the_audit_log(api, owner):
    http, _ = api
    project_id = http.post("/v1/projects", json={"name": "Audited"}).json()["id"]

    with owner.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT * FROM audit_log WHERE resource_id = :p AND action = 'project.created'"
            ),
            {"p": project_id},
        ).first()
    assert row is not None
    assert row.org_id == ORG
    assert row.actor_user_id == "usr_test_owner"
    assert row.resource_type == "project"


def test_a_viewer_cannot_create_one(api, viewer_token):
    http, _ = api
    refused = http.post(
        "/v1/projects",
        json={"name": "Not mine to make"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert refused.status_code == 403


def test_it_belongs_to_the_caller_and_nobody_else(api, other_tenant):
    """The other tenant's session must not see, or read back, this project."""
    http, _ = api
    project_id = http.post("/v1/projects", json={"name": "Ours"}).json()["id"]

    theirs = {"Authorization": f"Bearer {other_tenant}"}
    assert http.get(f"/v1/projects/{project_id}", headers=theirs).status_code == 404
    assert project_id not in [
        p["id"] for p in http.get("/v1/projects", headers=theirs).json()
    ]


def test_creating_without_a_session_is_refused(api):
    http, _ = api
    refused = http.post(
        "/v1/projects", json={"name": "Anonymous"}, headers={"Authorization": ""}
    )
    assert refused.status_code == 401
