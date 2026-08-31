"""Joining an organisation by invitation, not by signing up.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-31

An organisation holds unreleased footage and membership is the whole of the
access model (`routers/org.py`). Until now an owner added a member by creating
their account and choosing their password — which works, and means the owner
knows the password, and means somebody has to send it to them by some channel
that is not this system.

An invitation is the same decision made by the same person, with the account
created by the person it belongs to.

## The token is not stored

`token_hash` holds sha256 of it, exactly as `sessions` does (migration 0003).
The token exists in the email and in the invitee's URL bar and nowhere else, so
a leaked database does not hand anyone a way into an organisation. `unique` on
the hash rather than on the token: a collision is the interesting event and it
should fail loudly.

## The one policy escape, and why it is narrower than it looks

Accepting happens before there is a session, so the request has no tenant —
and every policy in this schema fails closed on an unset `app.org_id`. The
invitation row is therefore readable when its `token_hash` equals
`app.invitation_token`, the same shape as the session escape 0003 added, and
for the same reason: the caller proves which single row it is entitled to by
presenting the secret that names it.

`WITH CHECK` stays org-only. The escape reads; it never writes. Creating the
user, the credential and the session on acceptance all happen inside the org,
after `set_org`, exactly as sign-up does.

## Expiry and single use are columns, not conventions

`expires_at` and `accepted_at` are on the row because "this invitation is still
good" has to be answerable by the database rather than by whichever code path
happens to check. A used invitation is kept rather than deleted: who joined,
when, and on whose invitation is an access-control question with an audit
answer, and deleting the row throws it away.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from conventions import APP_ROLE, POLICY, create_org_table

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

TS = sa.TIMESTAMP(timezone=True)

_CURRENT_ORG = "nullif(current_setting('app.org_id', true), '')"
_INVITE_TOKEN = "nullif(current_setting('app.invitation_token', true), '')"


def upgrade() -> None:
    create_org_table(
        "invitations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        # sha256 of the token. The token itself is in an email and nowhere else.
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        # Who invited them. Not a foreign key: an invitation outlives the
        # person who sent it, and that person leaving is not a reason to lose
        # the record of who let somebody in.
        sa.Column("invited_by", sa.Text()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("accepted_at", TS),
        sa.Column("accepted_user_id", sa.Text()),
        sa.Column("revoked_at", TS),
        sa.Column("created_at", TS, nullable=False, server_default=sa.text("now()")),
    )

    # The narrow escape. Same shape as `sessions` in 0003: a caller with no
    # tenant may read exactly the row whose secret it presented.
    op.execute(f"DROP POLICY {POLICY} ON invitations")
    op.execute(
        f"CREATE POLICY {POLICY} ON invitations "
        f"USING (org_id = {_CURRENT_ORG} OR token_hash = {_INVITE_TOKEN}) "
        f"WITH CHECK (org_id = {_CURRENT_ORG})"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO {APP_ROLE}")

    # "Is there already an invitation out for this address?" is the question
    # the invite endpoint asks before creating a second one.
    op.create_index("ix_invitations_org_email", "invitations", ["org_id", "email"])

    op.execute(
        "COMMENT ON TABLE invitations IS "
        "'An offer of membership. The token is not stored — token_hash is "
        "sha256 of it, as with sessions. Readable without a tenant only by "
        "presenting that token (app.invitation_token); writes are always "
        "inside the org.'"
    )


def downgrade() -> None:
    op.drop_index("ix_invitations_org_email", table_name="invitations")
    op.drop_table("invitations")
