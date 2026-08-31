"""Projects: the container an org's assets, jobs and spend hang off.

Reads go through `Store`, so a local process serving fixtures and a deployment
talking to Postgres take the same code path. The one write does not: a fixture
cannot be written to, which is what `writable_db` refuses on rather than
failing somewhere inside psycopg.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .. import audit
from ..auth.sessions import Principal
from ..db import projects as project_writes, repository
from ..deps import require_write, writable_db
from ..logging import get_logger
from ..schemas import Asset, CreateProjectRequest, Job, Project
from ..store import Store, get_store

router = APIRouter(prefix="/v1", tags=["projects"])

log = get_logger(__name__)

#: Long enough for "Harbour Lights — Ep. 3 (reshoots, day 2)", short enough
#: that the project list stays readable. A bound belongs here rather than only
#: on the column: the failure without one is a pasted brief becoming a row that
#: breaks every list it appears in, which is not a database error to catch.
NAME_MAX = 120


def _name(raw: str) -> str:
    """The name as it will be stored: trimmed, with runs of whitespace collapsed.

    A name is a label in a list. Leading spaces and an embedded newline are
    paste artefacts, and storing them means two projects that look identical
    on screen and sort apart.
    """
    name = " ".join(raw.split())
    if not name:
        raise HTTPException(422, "a project needs a name")
    if len(name) > NAME_MAX:
        raise HTTPException(422, f"a project name is at most {NAME_MAX} characters")
    return name


@router.get("/projects", response_model=list[Project])
async def list_projects(store: Store = Depends(get_store)) -> list[Project]:
    return store.list_projects()


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(
    body: CreateProjectRequest,
    request: Request,
    principal: Principal = Depends(require_write),
    s: Session = Depends(writable_db),
) -> Project:
    """Create a project.

    A name and nothing else — everything else a project shows is derived from
    the rows underneath it. Duplicate names are allowed on purpose: two
    episodes really can be called "Day 1", and refusing the second is a rule
    the customer did not ask for and cannot override.
    """
    name = _name(body.name)
    org_id = principal.org_id

    project_id = project_writes.create(
        s, org_id, name=name, created_by=principal.user_id
    )
    audit.record(
        s, org_id, audit.PROJECT_CREATED, resource_type="project",
        resource_id=project_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    # No name in the log line: `mishne.logging` carries identifiers and status,
    # never customer content, and a project name is the customer's.
    log.info("project.created", project_id=project_id)

    project = repository.get_project(s, org_id, project_id)
    if project is None:  # pragma: no cover
        raise HTTPException(500, "the project was created but could not be read back")
    return project


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str, store: Store = Depends(get_store)) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    return project


@router.get("/projects/{project_id}/assets", response_model=list[Asset])
async def list_assets(project_id: str, store: Store = Depends(get_store)) -> list[Asset]:
    return store.list_assets(project_id)


@router.get("/projects/{project_id}/jobs", response_model=list[Job])
async def list_jobs(project_id: str, store: Store = Depends(get_store)) -> list[Job]:
    return store.list_jobs(project_id)
