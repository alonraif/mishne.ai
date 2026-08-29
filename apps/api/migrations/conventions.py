"""Helpers that make the expand/contract and RLS rules the path of least resistance.

Read README.md in this directory first. This module is the enforcement of what
that document describes: if every table goes through `create_org_table`, no table
can ship without `org_id NOT NULL`, without RLS forced on, and without a policy.

Deliberately dependency-free and importable from any migration script.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

#: The role the application connects as. Created by migration 0001. NOLOGIN —
#: real login users are granted membership out of band, so no credential ever
#: appears in a migration. See README.md.
APP_ROLE = "mishne_app"

#: Session variable the policies key on. Set per transaction with
#: `set_config('app.org_id', :org, true)`.
ORG_SETTING = "app.org_id"

POLICY = "org_isolation"

#: `nullif(..., '')` so that an explicitly blank value fails closed exactly like
#: an unset one. `current_setting(name, true)` returns NULL rather than raising
#: when the variable was never set — which is what makes an org-less request see
#: nothing at all.
_CURRENT_ORG = f"nullif(current_setting('{ORG_SETTING}', true), '')"

FULL_DML = "SELECT, INSERT, UPDATE, DELETE"
APPEND_ONLY = "SELECT, INSERT"


def create_org_table(
    name: str,
    *columns: sa.schema.SchemaItem,
    org_column: bool = True,
    key: str = "org_id",
    grants: str = FULL_DML,
    **kw,
) -> None:
    """Create a table, then enable and force RLS on it in the same breath.

    `org_id text NOT NULL` is injected unless `org_column=False`, which is only
    correct for `orgs` itself, where the tenant key is the primary key.

    NOT NULL here is not a violation of the expand/contract rule. That rule is
    about adding a column to a table an older release is already writing to;
    there is no older release writing to a table created in this migration.
    """
    cols: list[sa.schema.SchemaItem] = list(columns)
    if org_column:
        # Second position, right after the id, so the column ordering in \d is
        # the same on every table and a missing org_id is visible at a glance.
        cols.insert(1, sa.Column("org_id", sa.Text(), nullable=False))
    op.create_table(name, *cols, **kw)
    enable_rls(name, key=key)
    grant(name, grants)


def enable_rls(table: str, key: str = "org_id") -> None:
    """Enable, FORCE, and add the tenant policy.

    FORCE matters: without it the table's owner — which is the role migrations
    run as — is exempt from its own policies, and every isolation test written
    against the owner connection passes while proving nothing.
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {POLICY} ON {table} "
        f"USING ({key} = {_CURRENT_ORG}) "
        f"WITH CHECK ({key} = {_CURRENT_ORG})"
    )


def disable_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def grant(table: str, privileges: str = FULL_DML) -> None:
    op.execute(f"GRANT {privileges} ON {table} TO {APP_ROLE}")


def concurrent_index(name: str, table: str, columns: list[str], **kw) -> None:
    """CREATE INDEX CONCURRENTLY, in the autocommit block it requires.

    `CONCURRENTLY` cannot run inside a transaction, and Alembic wraps a
    migration in one. A failed concurrent build leaves an INVALID index behind:

        select indexrelid::regclass from pg_index where not indisvalid;
    """
    with op.get_context().autocommit_block():
        op.create_index(
            name, table, columns, postgresql_concurrently=True, if_not_exists=True, **kw
        )


def drop_concurrent_index(name: str, table: str) -> None:
    with op.get_context().autocommit_block():
        op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)


def append_only(table: str) -> None:
    """Refuse UPDATE and DELETE at the database, not in application code.

    The credit ledger is auditable only if nothing can quietly rewrite it, and
    "nobody would do that" is not an access control (ADR-0006). Correct a
    mistake with a compensating `adjustment` row.
    """
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {table}_is_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{table} is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        f"CREATE TRIGGER {table}_append_only "
        f"BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {table}_is_append_only()"
    )


def drop_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute(f"DROP FUNCTION IF EXISTS {table}_is_append_only()")
