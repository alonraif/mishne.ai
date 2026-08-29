"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Checklist — see migrations/README.md:
  [ ] Safe with the PREVIOUS release still running? (expand only; no rename,
      no NOT NULL without a default, no tightened constraint)
  [ ] New table? Created with conventions.create_org_table, so it has
      org_id NOT NULL, RLS enabled, RLS FORCEd, and a policy.
  [ ] New index? conventions.concurrent_index.
  [ ] downgrade() actually reverses this. Tested, not assumed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from conventions import (  # noqa: F401
    concurrent_index,
    create_org_table,
    drop_concurrent_index,
)

revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
