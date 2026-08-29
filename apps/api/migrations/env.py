"""Alembic environment.

────────────────────────────────────────────────────────────────────────────
  BEFORE YOU WRITE A MIGRATION, READ migrations/README.md.

  The short version, because it is the part that gets forgotten:

  * Every migration runs while the PREVIOUS release is still serving traffic.
    Expand, dual-write, backfill, read-new, contract — across releases.
  * No NOT NULL without a default on an existing table. No renames. Ever.
  * Indexes CONCURRENTLY, inside an autocommit block.
  * `downgrade` must genuinely work. `alembic downgrade base` is in CI.
  * Every table gets `org_id text NOT NULL` and RLS, in the migration that
    CREATES it. Use conventions.create_org_table; if you are calling
    op.create_table directly you are shipping a table with no policy.
────────────────────────────────────────────────────────────────────────────

The database URL is not read from alembic.ini. It comes from mishne.config, so
the migration runner and the application can never be pointed at different
databases by accident. Override with `-x url=...` or the DATABASE_URL
environment variable.

Migrations connect as the schema OWNER. The application connects as the
restricted `mishne_app` role. See README.md — testing isolation as the owner
proves nothing, because the owner is exempt from its own policies unless the
table is FORCEd (which conventions.py does).
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Resolved from this file rather than from the working directory, so `alembic`
# works from anywhere — including from a test that never chdir'd into apps/api.
_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

from mishne.db.base import normalise_url  # noqa: E402
from mishne.db.models import Base  # noqa: E402

target_metadata = Base.metadata


def _url() -> str:
    override = context.get_x_argument(as_dictionary=True).get("url")
    if override:
        return normalise_url(override)
    from mishne.config import get_settings

    return normalise_url(get_settings().database_url)


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # Policies, triggers and grants are invisible to autogenerate. It is
            # a starting point for a diff, never the whole migration.
            include_schemas=False,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
