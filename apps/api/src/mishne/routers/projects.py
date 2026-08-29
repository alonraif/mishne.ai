from fastapi import APIRouter, Depends, HTTPException

from ..schemas import Asset, CreateProjectRequest, Job, Project
from ..store import Store, get_store

router = APIRouter(prefix="/v1", tags=["projects"])


@router.get("/projects", response_model=list[Project])
async def list_projects(store: Store = Depends(get_store)) -> list[Project]:
    return store.list_projects()


@router.post("/projects", response_model=Project, status_code=201)
async def create_project(body: CreateProjectRequest) -> Project:
    raise HTTPException(501, "not implemented")


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
