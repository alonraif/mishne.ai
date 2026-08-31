"""A platform back-office, outside the tenant model rather than inside it.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-31

Every table in this schema is isolated by `app.org_id`, and the two escapes that
exist (sessions in 0003, invitations in 0008) are narrow, read-only, and keyed
on a secret the caller presents. A back-office is the opposite shape: it is
cross-tenant by definition, and it writes.

## Why this does not add a third policy escape

The obvious move is a `platform_admins` table and an `app.is_platform_admin`
setting that every policy also accepts. That was rejected. It puts a second
clause into every policy in the database — the clause that decides whether one
customer can see another's unreleased footage — and it puts the code that can
set that clause inside the process that answers requests from the internet. A
bug there is a cross-tenant leak in the product everyone talks to.

Instead the back-office is a **separate process on a separate connection**, and
the privilege lives in the database role rather than in a request variable.
`mishne_admin` is created here as a NOLOGIN privilege bundle, the same shape as
`mishne_app` in 0001 — and, unlike `mishne_app`, the login role granted it must
also carry BYPASSRLS.

**BYPASSRLS is a role attribute and attributes are not inherited through
membership**, so granting `mishne_admin` does not by itself confer it: the login
role has to be created with it. That is deliberate. It cannot be done by
accident, it is visible in `pg_roles`, and `mishne.admin.main` asserts it at
startup and refuses to serve without it — an admin process quietly filtered by
RLS would show an empty list of organisations, which reads as "no customers"
rather than as "misconfigured".

    CREATE ROLE mishne_admin_local LOGIN PASSWORD '...' BYPASSRLS;
    GRANT mishne_admin TO mishne_admin_local;

## The platform tables are not reachable from the product

`platform_admins`, `platform_sessions` and `platform_actions` are granted to
`mishne_admin` and to nobody else. `mishne_app` — the role the customer-facing
API connects as — has no privilege on them at all, so a bug in that API cannot
read a platform credential or forge a platform session: the failure is
`permission denied for table`, from Postgres, before any application code runs.

They also carry RLS with **no policy at all**, which denies every row to every
role that is not exempt. Belt and braces, and the braces are the interesting
half: it means that if somebody later grants `mishne_app` access to these tables
by mistake, that grant still reads nothing.

## Suspension is a column here and a refusal in `sessions.resolve`

`orgs.suspended_at` is the only change to an existing table. A suspended
organisation's sessions stop resolving, so suspending is locking the tenant out
rather than a flag on a screen — see `auth/sessions.py`.

## What the back-office did is its own log, not the customers'

`audit_log` is per organisation and is disclosed to that customer on request; a
platform action is a record of what *we* did, across tenants, and belongs
somewhere a customer's retention policy does not reach. It is append-only for
the same reason `audit_log` is, and every mutating endpoint requires a `reason`
— "who gave this org 500 credits, and why" is the question this table exists to
answer, and a nullable reason is a question it cannot answer six months later.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import INET, JSONB

from conventions import append_only, drop_append_only

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")

ADMIN_ROLE = "mishne_admin"

#: Every kind of thing the back-office can do. Closed, like `audit.py`'s
#: vocabulary and for the same reason: a free-text action column is unqueryable
#: within a month.
ACTIONS = (
    "admin.login",
    "admin.login_failed",
    "admin.logout",
    "admin.created",
    "credits.granted",
    "credits.adjusted",
    "org.tier_changed",
    "org.retention_changed",
    "org.suspended",
    "org.unsuspended",
    "org.deleted",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def _locked_down(table: str) -> None:
    """Grant to the admin role only, and deny every row to everyone else.

    RLS with no policy is not an oversight. `ENABLE` plus `FORCE` and no
    `CREATE POLICY` means the table has no rows for any role without BYPASSRLS,
    including the role that owns it. The grant is what the admin process needs;
    the policy-less RLS is what survives somebody adding a grant later.
    """
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {ADMIN_ROLE}")
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def upgrade() -> None:
    # The privilege bundle. NOLOGIN for the same reason `mishne_app` is: a role
    # with a password in a migration is a credential in version control.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mishne_admin') THEN
                CREATE ROLE mishne_admin NOLOGIN;
            END IF;
        END
        $$;
        """
    )
    # Read and write on the customer tables. The back-office adjusts balances,
    # changes tiers and deletes organisations; RLS is what it is exempt from,
    # not the grant system.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {ADMIN_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {ADMIN_ROLE}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {ADMIN_ROLE}")
    # And on whatever later migrations create. Without this, the first table
    # added in 0010 is invisible to the back-office, and the symptom is one
    # screen throwing `permission denied` months after this migration ran —
    # by which time nobody is looking here. The default applies to objects
    # created by the role migrations run as, which is the role running this.
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {ADMIN_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"GRANT USAGE, SELECT ON SEQUENCES TO {ADMIN_ROLE}"
    )

    # ─────────────────────────────────────────────────────────── the accounts

    op.create_table(
        "platform_admins",
        sa.Column("id", sa.Text, primary_key=True),
        # Not a foreign key to `users`, and deliberately not the same table. A
        # platform admin is not a member of any organisation, cannot sign into
        # the product with this credential, and cannot be created by promoting
        # a customer account — there is no flag for a bug to flip.
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False, server_default=sa.text("''")),
        # scrypt, same encoding as `credentials` — see auth/passwords.py.
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        # Who made this one. Null for the first, which the bootstrap CLI
        # creates because there is nobody to attribute it to yet.
        sa.Column("created_by", sa.Text),
        sa.Column("last_login_at", TS),
        # Disabled rather than deleted: what a departed admin did stays
        # attributable, and `platform_actions.admin_id` still resolves.
        sa.Column("disabled_at", TS),
    )
    _locked_down("platform_admins")

    op.create_table(
        "platform_sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "admin_id",
            sa.Text,
            sa.ForeignKey("platform_admins.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # sha256 of the token, never the token. Same rule as `sessions` and
        # `invitations`: a leaked database is not a set of keys.
        sa.Column("token_hash", sa.Text, nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.Column("ip", INET),
    )
    _locked_down("platform_sessions")
    op.create_index("ix_platform_sessions_admin", "platform_sessions", ["admin_id"])

    # ──────────────────────────────────────────────────────────── the log

    op.create_table(
        "platform_actions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("admin_id", sa.Text),
        sa.Column("action", sa.Text, nullable=False),
        # Which tenant it was done to. Null for the actions that are about the
        # back-office itself — a login, an admin being created.
        sa.Column("target_org_id", sa.Text),
        sa.Column("target_type", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("target_id", sa.Text),
        # Why. Required by every mutating endpoint; see the module docstring.
        sa.Column("reason", sa.Text, nullable=False, server_default=sa.text("''")),
        # Numbers and ids only — never customer content. Same rule as
        # `audit_log`, and this table is retained longer than most of them.
        sa.Column("detail", JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("ip", INET),
        sa.Column("user_agent", sa.Text),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.CheckConstraint(_in("action", ACTIONS), name="ck_platform_actions_action"),
    )
    _locked_down("platform_actions")
    op.create_index(
        "ix_platform_actions_created", "platform_actions", ["created_at"]
    )
    op.create_index(
        "ix_platform_actions_org", "platform_actions", ["target_org_id", "created_at"]
    )
    append_only("platform_actions")

    # ───────────────────────────────────────────────────────────  suspension

    # Nullable, with no default: an existing organisation is not suspended, and
    # expand/contract forbids a NOT NULL column on a table older releases are
    # already writing to.
    op.add_column("orgs", sa.Column("suspended_at", TS))
    op.add_column("orgs", sa.Column("suspended_reason", sa.Text))

    op.execute(
        "COMMENT ON TABLE platform_admins IS "
        "'Platform staff. Not customers, not rows in users, and not reachable "
        "from the mishne_app role — see migration 0009.'"
    )
    op.execute(
        "COMMENT ON TABLE platform_actions IS "
        "'What the back-office did, across tenants. Append-only. Ids and "
        "numbers only, never customer content.'"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM {ADMIN_ROLE}"
    )
    op.execute(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        f"REVOKE USAGE, SELECT ON SEQUENCES FROM {ADMIN_ROLE}"
    )
    op.drop_column("orgs", "suspended_reason")
    op.drop_column("orgs", "suspended_at")
    drop_append_only("platform_actions")
    op.drop_index("ix_platform_actions_org", table_name="platform_actions")
    op.drop_index("ix_platform_actions_created", table_name="platform_actions")
    op.drop_table("platform_actions")
    op.drop_index("ix_platform_sessions_admin", table_name="platform_sessions")
    op.drop_table("platform_sessions")
    op.drop_table("platform_admins")
    # The role is left in place: it may still own grants elsewhere, and
    # dropping a role out from under a live connection is not a downgrade.
