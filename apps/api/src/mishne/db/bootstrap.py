"""Create the local login role the API connects as, without needing psql.

    python -m mishne.db.bootstrap

Runs `infra/local-app-user.sql` as the owner. That file creates `mishne_local`
and grants it the `mishne_app` role that migration 0001 creates — so run this
*after* `alembic upgrade head`.

It exists because `psql` is not installed on most machines that can run this
project perfectly well, and "install the Postgres client tools" is a poor first
step in a setup guide. Local only: staging and production create their login
user through secrets management, with a password worth protecting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa

from ..config import get_settings
from .base import normalise_url

SQL_FILE = "infra/local-app-user.sql"


def _find_sql() -> Path:
    """Walk up from this file to the repository root."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / SQL_FILE
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"could not find {SQL_FILE} above {__file__}")


def bootstrap() -> Path:
    settings = get_settings()
    if settings.environment != "local":
        raise RuntimeError(
            f"refusing to create a well-known local credential in "
            f"environment={settings.environment!r}"
        )

    path = _find_sql()
    # The owner connection: creating a role needs privileges the application
    # role does not have, and must not have.
    engine = sa.create_engine(normalise_url(settings.database_url))
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            # One call, not split on semicolons: the file contains a DO $$ ... $$
            # block whose body has semicolons of its own.
            conn.exec_driver_sql(path.read_text())
    finally:
        engine.dispose()
    return path


def main() -> int:
    try:
        path = bootstrap()
    except Exception as exc:  # noqa: BLE001 — this is a CLI, the message is the output
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(f"applied {path.name}; the API can now connect as mishne_local")
    return 0


if __name__ == "__main__":
    sys.exit(main())
