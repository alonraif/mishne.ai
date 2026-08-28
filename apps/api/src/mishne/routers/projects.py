from fastapi import APIRouter, HTTPException

from .. import mock
from ..schemas import Asset, CreateProjectRequest, Job, Project

router = APIRouter(prefix="/v1", tags=["projects"])


@router.get("/projects", response_model=list[Project])
async def list_projects() -> list[Project]:
    return mock.PROJECTS


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(body: CreateProjectRequest) -> Project:
    raise HTTPException(501, "not implemented")


@router.get("/projects/{project_id}", response_model=Project)
async def get_project(project_id: str) -> Project:
    for p in mock.PROJECTS:
        if p.id == project_id:
            return p
    raise HTTPException(404, "project not found")


@router.get("/projects/{project_id}/assets", response_model=list[Asset])
async def list_assets(project_id: str) -> list[Asset]:
    return [a for a in mock.ASSETS if a.project_id == project_id]


@router.get("/projects/{project_id}/jobs", response_model=list[Job])
async def list_jobs(project_id: str) -> list[Job]:
    return [j for j in mock.JOBS if j.project_id == project_id]
