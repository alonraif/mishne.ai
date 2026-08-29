"""Credentials, sessions, and the two narrow ways in before an org is known.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-29

Workstream B4. The schema half of identity, and one genuinely interesting
problem: **row-level security keys on `app.org_id`, and authentication is what
determines `app.org_id`.** A request arrives with a session token and nothing
else. Reading the session row to learn the org requires seeing a row, and seeing
a row requires already knowing the org.

The obvious answers are all bad. A `BYPASSRLS` role for the auth path puts a
credential in the system whose whole purpose is to ignore tenancy. A
`SECURITY DEFINER` function owned by the schema owner is exempt only while
`FORCE ROW LEVEL SECURITY` is off, and turning that off is the mistake 0001 was
written to prevent. A policy that allows reads when `app.org_id` is unset makes
"forgot to set the org" a full table scan of every tenant instead of an empty
result.

So the policies here are widened by exactly one clause each, and each clause is
keyed on **something the caller has already presented**:

* `sessions` is readable when `token_hash` equals `app.session_token`. A request
  can therefore see the session row for the token it holds and no other. This is
  a *narrower* grant than the org policy, not a wider one — a leaked org id
  reads nothing here.
* `users` is readable when `lower(email)` equals `app.login_email`, which is the
  one row a login attempt needs before it knows anything.
* `user_credentials` is readable when `user_id` equals `app.login_user`, set
  only after the row above has been read.

Each is set with `set_config(..., is_local => true)`, so it lives for one
transaction and cannot survive onto the next request that borrows the same
pooled connection.

## Global email uniqueness

`uq_users_org_email` makes an address unique within an org, which means a login
by email alone is ambiguous across orgs. This migration adds a unique index on
`lower(email)` across all orgs, so an address identifies exactly one person.

This is a *tightening*, which the expand/contract rules forbid — with one
condition, met here: the constraint cannot reject a row that exists, because
authentication does not exist yet and `users` has no rows outside development
seeds. Supporting one person in two organisations later means a `memberships`
table and dropping this index, which is a contract step with its own release.
See ADR-0015.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from conventions import (
    APP_ROLE,
    ORG_SETTING,
    POLICY,
    concurrent_index,
    create_org_table,
    drop_concurrent_index,
)

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)
NOW = sa.text("now()")

#: Spelled out locally: a migration is a historical record and must keep
#: describing this date's schema after somebody appends to the live module.
AUTH_PROVIDERS = ("local", "workos")

_CURRENT_ORG = f"nullif(current_setting('{ORG_SETTING}', true), '')"
_SESSION_TOKEN = "nullif(current_setting('app.session_token', true), '')"
_LOGIN_EMAIL = "nullif(current_setting('app.login_email', true), '')"
_LOGIN_USER = "nullif(current_setting('app.login_user', true), '')"


def upgrade() -> None:
    # ── how a user proves who they are ─────────────────────────────────────
    #
    # Separate from `users` because a password is not an attribute of a person,
    # it is one way of authenticating them: an org on SSO has users with no row
    # here at all, and deleting the credential must not delete the account.
    create_org_table(
        "user_credentials",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # The full encoded form: algorithm, parameters, salt and digest. Storing
        # the parameters with the hash is what makes them changeable later
        # without invalidating every existing password.
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("updated_at", TS, nullable=False, server_default=NOW),
        sa.UniqueConstraint("user_id", name="uq_user_credentials_user"),
    )

    # ── the session ────────────────────────────────────────────────────────
    #
    # The token itself is never stored: only its SHA-256. A dump of this table
    # is then a list of session ids rather than a set of working credentials,
    # which is the same reason a password is not stored either.
    create_org_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", TS, nullable=False, server_default=NOW),
        sa.Column("last_seen_at", TS, nullable=False, server_default=NOW),
        sa.Column("expires_at", TS, nullable=False),
        # Revoked rather than deleted: "when did this session end, and was it a
        # logout or an expiry" is a question a security review asks.
        sa.Column("revoked_at", TS),
        sa.UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
    )
    concurrent_index("ix_sessions_user", "sessions", ["org_id", "user_id"])

    # ── which mechanism authenticated this user ────────────────────────────
    #
    # Nullable, so the release that does not know about it keeps inserting.
    op.add_column("users", sa.Column("auth_provider", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_users_auth_provider",
        "users",
        "auth_provider IS NULL OR auth_provider IN ("
        + ", ".join(f"'{p}'" for p in AUTH_PROVIDERS)
        + ")",
    )

    # One address, one person. See the module docstring for why a tightening is
    # permissible exactly here.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS ux_users_email_lower "
            "ON users (lower(email))"
        )

    # ── the two ways in ────────────────────────────────────────────────────
    #
    # A policy is replaced rather than altered because Postgres has no way to
    # widen one in place. Both replacements are supersets of what 0001 created,
    # so a release running against either version behaves identically for every
    # request that has an org.
    op.execute(f"DROP POLICY {POLICY} ON users")
    op.execute(
        f"CREATE POLICY {POLICY} ON users "
        f"USING (org_id = {_CURRENT_ORG} OR lower(email) = {_LOGIN_EMAIL}) "
        f"WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    op.execute(f"DROP POLICY {POLICY} ON sessions")
    op.execute(
        f"CREATE POLICY {POLICY} ON sessions "
        f"USING (org_id = {_CURRENT_ORG} OR token_hash = {_SESSION_TOKEN}) "
        f"WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    op.execute(f"DROP POLICY {POLICY} ON user_credentials")
    op.execute(
        f"CREATE POLICY {POLICY} ON user_credentials "
        f"USING (org_id = {_CURRENT_ORG} OR user_id = {_LOGIN_USER}) "
        f"WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    # WITH CHECK stays org-only on all three: a write must always name the org
    # it belongs to, and none of the escapes above may be used to insert.
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON user_credentials TO {APP_ROLE}")


def downgrade() -> None:
    op.execute(f"DROP POLICY {POLICY} ON user_credentials")
    op.execute(
        f"CREATE POLICY {POLICY} ON user_credentials "
        f"USING (org_id = {_CURRENT_ORG}) WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    op.execute(f"DROP POLICY {POLICY} ON sessions")
    op.execute(
        f"CREATE POLICY {POLICY} ON sessions "
        f"USING (org_id = {_CURRENT_ORG}) WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    op.execute(f"DROP POLICY {POLICY} ON users")
    op.execute(
        f"CREATE POLICY {POLICY} ON users "
        f"USING (org_id = {_CURRENT_ORG}) WITH CHECK (org_id = {_CURRENT_ORG})"
    )

    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ux_users_email_lower")
    op.drop_constraint("ck_users_auth_provider", "users", type_="check")
    op.drop_column("users", "auth_provider")

    drop_concurrent_index("ix_sessions_user", "sessions")
    op.drop_table("sessions")
    op.drop_table("user_credentials")
