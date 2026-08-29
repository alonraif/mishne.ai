"""One read interface, two backings: Postgres, and the fixtures.

The routers depend on this and never on either implementation, so `use_mocks`
is decided once, in a dependency, instead of in an `if` at the top of every
endpoint. That matters for more than tidiness: an endpoint that forgets the `if`
serves fixtures in production, and this shape makes forgetting impossible.

`Settings` refuses to construct with `use_mocks=True` outside `local`, so the
fixture path cannot be reached where there is real data.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from fastapi import Depends
from sqlalchemy.orm import Session

from . import mock
from .config import Settings, get_settings
from .db import repository
from .db.base import session_for_org
from .deps import current_org
from .schemas import Artifact, Asset, Job, LedgerEntry, Org, Project, Transcript


class Store(Protocol):
    def list_projects(self) -> list[Project]: ...
    def get_project(self, project_id: str) -> Project | None: ...
    def list_assets(self, project_id: str) -> list[Asset]: ...
    def get_asset(self, asset_id: str) -> Asset | None: ...
    def list_jobs(self, project_id: str) -> list[Job]: ...
    def get_job(self, job_id: str) -> Job | None: ...
    def list_artifacts(self, job_id: str) -> list[Artifact]: ...
    def get_transcript(self, job_id: str) -> Transcript | None: ...
    def get_org(self) -> Org | None: ...
    def list_ledger(self, project_id: str | None = None) -> list[LedgerEntry]: ...


class MockStore:
    """The fixtures in `mock.py`, filtered the way the database would filter.

    It honours `org_id` even though there is only one org in the fixtures. A
    mock that ignores tenancy trains everyone reading it to forget tenancy.
    """

    def __init__(self, org_id: str) -> None:
        self.org_id = org_id

    def _mine(self) -> bool:
        return self.org_id == mock.ORG.id

    def list_projects(self) -> list[Project]:
        return list(mock.PROJECTS) if self._mine() else []

    def get_project(self, project_id: str) -> Project | None:
        return next((p for p in self.list_projects() if p.id == project_id), None)

    def list_assets(self, project_id: str) -> list[Asset]:
        if not self._mine():
            return []
        return [a for a in mock.ASSETS if a.project_id == project_id]

    def get_asset(self, asset_id: str) -> Asset | None:
        if not self._mine():
            return None
        return next((a for a in mock.ASSETS if a.id == asset_id), None)

    def list_jobs(self, project_id: str) -> list[Job]:
        if not self._mine():
            return []
        return [j for j in mock.JOBS if j.project_id == project_id]

    def get_job(self, job_id: str) -> Job | None:
        if not self._mine():
            return None
        return next((j for j in mock.JOBS if j.id == job_id), None)

    def list_artifacts(self, job_id: str) -> list[Artifact]:
        if not self._mine():
            return []
        return [a for a in mock.ARTIFACTS if a.job_id == job_id]

    def get_transcript(self, job_id: str) -> Transcript | None:
        return mock.transcript_for(job_id) if self._mine() else None

    def get_org(self) -> Org | None:
        return mock.ORG if self._mine() else None

    def list_ledger(self, project_id: str | None = None) -> list[LedgerEntry]:
        if not self._mine():
            return []
        if project_id:
            return [e for e in mock.LEDGER if e.project_id == project_id]
        return list(mock.LEDGER)


class SqlStore:
    """Postgres, through a session already scoped to one org by RLS."""

    def __init__(self, session: Session, org_id: str) -> None:
        self.s = session
        self.org_id = org_id

    def list_projects(self) -> list[Project]:
        return repository.list_projects(self.s, self.org_id)

    def get_project(self, project_id: str) -> Project | None:
        return repository.get_project(self.s, self.org_id, project_id)

    def list_assets(self, project_id: str) -> list[Asset]:
        return repository.list_assets(self.s, self.org_id, project_id)

    def get_asset(self, asset_id: str) -> Asset | None:
        return repository.get_asset(self.s, self.org_id, asset_id)

    def list_jobs(self, project_id: str) -> list[Job]:
        return repository.list_jobs(self.s, self.org_id, project_id)

    def get_job(self, job_id: str) -> Job | None:
        return repository.get_job(self.s, self.org_id, job_id)

    def list_artifacts(self, job_id: str) -> list[Artifact]:
        return repository.list_artifacts(self.s, self.org_id, job_id)

    def get_transcript(self, job_id: str) -> Transcript | None:
        return repository.get_transcript(self.s, self.org_id, job_id)

    def get_org(self) -> Org | None:
        return repository.get_org(self.s, self.org_id)

    def list_ledger(self, project_id: str | None = None) -> list[LedgerEntry]:
        return repository.list_ledger(self.s, self.org_id, project_id)


def get_store(
    org_id: str = Depends(current_org),
    settings: Settings = Depends(get_settings),
) -> Iterator[Store]:
    """The store for this request. No database connection is opened for mocks."""
    if settings.use_mocks:
        yield MockStore(org_id)
        return
    with session_for_org(org_id) as session:
        yield SqlStore(session, org_id)
