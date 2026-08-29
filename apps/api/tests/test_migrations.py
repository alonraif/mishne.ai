"""`alembic upgrade head` builds the schema, and `alembic downgrade base` unbuilds it.

A downgrade that does not genuinely work is the most expensive kind of lie in
this project: in-flight jobs survive a deploy precisely so that a bad release can
be rolled back without losing anyone's work, and a rollback with no working
downgrade is not a rollback (ADR-0012).

This runs against a scratch database created and dropped by the test, so it
never touches the seeded development data.
"""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sa = pytest.importorskip("sqlalchemy")
command = pytest.importorskip("alembic.command")
Config = pytest.importorskip("alembic.config").Config

from conftest import requires_postgres  # noqa: E402

# Importing the models pulls in SQLAlchemy too, so it goes through the same gate.
ALL_TABLES = pytest.importorskip("mishne.db.models").ALL_TABLES

pytestmark = requires_postgres

API_ROOT = Path(__file__).parent.parent
SCRATCH_DB = "mishne_migration_scratch"


@pytest.fixture
def scratch(owner: sa.Engine) -> str:
    """An empty database, dropped afterwards whatever happens."""
    with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{SCRATCH_DB}"'))
    # `str(url)` and `render_as_string()` both mask the password as "***", which
    # arrives at the server as a literal password and fails authentication. The
    # sandbox this was first written against used trust auth, so it passed there
    # and only failed against docker-compose, which is the environment that
    # counts.
    url = owner.url.set(database=SCRATCH_DB)
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with owner.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}" WITH (FORCE)'))


def _alembic(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    # How `alembic -x url=...` is spelled when alembic is driven from Python.
    cfg.cmd_opts = Namespace(x=[f"url={url}"])
    return cfg


def _objects(url: str) -> dict[str, set[str]]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            tables = set(
                conn.execute(
                    sa.text(
                        "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                        "ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                ).scalars()
            )
            functions = set(
                conn.execute(
                    sa.text(
                        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n "
                        "ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
                    )
                ).scalars()
            )
            extensions = set(
                conn.execute(sa.text("SELECT extname FROM pg_extension")).scalars()
            )
            invalid = set(
                conn.execute(
                    sa.text(
                        "SELECT indexrelid::regclass::text FROM pg_index "
                        "WHERE NOT indisvalid"
                    )
                ).scalars()
            )
        return {
            "tables": tables,
            "functions": functions,
            "extensions": extensions,
            "invalid_indexes": invalid,
        }
    finally:
        engine.dispose()


def test_upgrade_then_downgrade_returns_an_empty_database(scratch: str) -> None:
    before = _objects(scratch)
    assert before["tables"] == set()

    command.upgrade(_alembic(scratch), "head")

    after = _objects(scratch)
    assert set(ALL_TABLES) <= after["tables"], (
        f"missing after upgrade: {set(ALL_TABLES) - after['tables']}"
    )
    assert "vector" in after["extensions"]
    # A CONCURRENTLY build that failed leaves an unusable index behind and no
    # error anywhere the deploy would notice.
    assert after["invalid_indexes"] == set()

    command.downgrade(_alembic(scratch), "base")

    end = _objects(scratch)
    # alembic_version is alembic's own bookkeeping and outlives `base`.
    assert end["tables"] <= {"alembic_version"}, f"left behind: {end['tables']}"
    assert end["functions"] == set(), f"functions left behind: {end['functions']}"
    assert "vector" not in end["extensions"]


def test_upgrade_is_repeatable_after_a_downgrade(scratch: str) -> None:
    """Rolling forward again after a rollback is the other half of a rollback."""
    cfg = _alembic(scratch)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert set(ALL_TABLES) <= _objects(scratch)["tables"]
