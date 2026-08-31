"""Creating a project — the one write against the `projects` table.

Reads live in `repository`, which computes `asset_count`, `job_count` and
`credits_used` as aggregates rather than storing them (ADR-0006). So there is
no counter to initialise here, and therefore none that can start out wrong: a
new project's three numbers are zero because it has no assets, no jobs and no
ledger rows, not because something wrote three zeroes.

`org_id` is passed rather than inferred. Every policy on the table is written
against `app.org_id`, so an insert with the wrong org is refused by the
database rather than accepted into somebody else's tenant.
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from sqlalchemy.orm import Session

from . import models as m


def create(s: Session, org_id: str, *, name: str, created_by: str | None) -> str:
    """Insert the row and return its id. The caller reads it back through the
    repository, so the response is the same shape the list endpoint returns."""
    project_id = f"prj_{secrets.token_hex(4)}"
    s.execute(
        sa.insert(m.Project.__table__).values(
            id=project_id,
            org_id=org_id,
            name=name,
            created_by=created_by,
        )
    )
    return project_id
