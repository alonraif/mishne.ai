"""The audit log: who did what, in a table that cannot be rewritten.

`docs/architecture/04-security.md` is the list of what belongs here. The shape
of the answer matters as much as having one: a broadcaster's security review
asks "who downloaded this material, and when", and an answer assembled from
application logs is not an answer, because logs rotate, are queryable by anyone
with the console, and can be written by any code path that feels like it.

Three rules, and all three are enforced somewhere other than good intentions:

**Append-only in the database.** Migration 0001 puts a trigger on the table that
raises on UPDATE and DELETE. "Nobody would rewrite it" is not an access control.

**No customer content, ever.** Same rule as `mishne.logging`, and for a stronger
reason: this table is retained for years and is disclosed to customers on
request. Identifiers, counts and status — never a filename, never brief text,
never a transcript line. `resource_id` is an id, and ids are opaque.

**The actor is the session's principal**, never a value from the request body. A
caller supplying its own actor id is writing somebody else's name into the
record of what it did.
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from sqlalchemy.orm import Session

from .db import models as m

# The vocabulary. Deliberately a small closed list — an audit log with a
# free-text action column becomes unqueryable within a month.
LOGIN = "user.login"
LOGIN_FAILED = "user.login_failed"
LOGOUT = "user.logout"
SIGNUP = "org.created"
MEMBER_ADDED = "member.added"
MEMBER_ROLE_CHANGED = "member.role_changed"
MEMBER_REMOVED = "member.removed"
UPLOAD_STARTED = "asset.upload_started"
UPLOAD_COMPLETED = "asset.upload_completed"
UPLOAD_CANCELLED = "asset.upload_cancelled"
ASSET_PROBED = "asset.probed"
ARTIFACT_DOWNLOADED = "artifact.downloaded"
JOB_CREATED = "job.created"
# Who decided what the piece contains. On a manual or hybrid job the cut is a
# person's editorial judgement rather than the system's, and "who chose this"
# is the question a disputed deliverable asks first.
JOB_CUT_SUBMITTED = "job.cut_submitted"
# Who a voice belongs to is a claim about a person, made by a person.
SPEAKER_RENAMED = "speaker.renamed"
SPEAKERS_MERGED = "speaker.merged"
RETENTION_CHANGED = "org.retention_changed"
# Billing. 04-security names permission and billing changes explicitly, and
# "who bought credits, and when" is the first question a disputed charge asks.
CHECKOUT_STARTED = "billing.checkout_started"
CREDITS_GRANTED = "billing.credits_granted"


def _inet(value: str | None) -> str | None:
    """An address the `inet` column will accept, or nothing.

    A proxy header is attacker-controlled and a test client sends a hostname.
    An audit row is worth having with a missing address and not worth failing a
    request over: writing an unparseable value raises, and the request that
    raises is the upload the customer was making.
    """
    if not value:
        return None
    import ipaddress

    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def record(
    s: Session,
    org_id: str,
    action: str,
    *,
    resource_type: str,
    resource_id: str | None = None,
    actor_user_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """One row. Never raises into the caller's path on a duplicate id."""
    s.execute(
        sa.insert(m.AuditLog.__table__).values(
            id=f"aud_{secrets.token_hex(8)}",
            org_id=org_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=_inet(ip),
            # Truncated: a user agent is attacker-controlled and unbounded, and
            # this column exists to distinguish browsers, not to archive them.
            user_agent=(user_agent or "")[:400] or None,
        )
    )


def record_even_if_the_request_fails(org_id: str, action: str, **kw) -> None:
    """An audit row in its own transaction, for a path that is about to raise.

    A failed login is the case that matters: the row is written inside the
    request's transaction, the request then raises a 401, and the rollback takes
    the row with it — so the one event a security review most wants to see is
    the one event that never gets recorded. This opens a second transaction that
    commits on its own.

    Best effort by construction: if this fails, the caller's failure is still
    the failure the user gets.
    """
    from .db.base import get_sessionmaker, set_org

    try:
        with get_sessionmaker().begin() as session:
            set_org(session, org_id)
            record(session, org_id, action, **kw)
    except Exception:  # noqa: BLE001 - never mask the caller's own error
        from .logging import get_logger

        get_logger(__name__).warning("audit.write_failed", action=action)


def client_ip(request) -> str | None:
    """The caller's address, as far as it can be trusted.

    Behind a load balancer the socket address is the balancer, and
    `X-Forwarded-For` is client-supplied and trivially spoofed — so only the
    LAST hop, which the balancer itself appended, is worth anything, and only
    when we know we are behind one. Until that is deployed (B3), the socket
    address is what is recorded.
    """
    client = getattr(request, "client", None)
    return getattr(client, "host", None)
