"""The back-office's own log.

`mishne.audit` records what a customer's people did inside their own
organisation, and that table is theirs — it is disclosed to them on request and
lives under their retention policy. This is the other half: what *we* did, to
whom, and why. It is append-only at the database (migration 0009), the
vocabulary is closed, and the `reason` is not optional.

The rule about content is the same and matters more here, because this table
outlives everything else: identifiers, numbers and status. Never a project
name, never a filename, never an email body.
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from sqlalchemy.orm import Session

from ..db import models as m

LOGIN = "admin.login"
LOGIN_FAILED = "admin.login_failed"
LOGOUT = "admin.logout"
ADMIN_CREATED = "admin.created"
CREDITS_GRANTED = "credits.granted"
CREDITS_ADJUSTED = "credits.adjusted"
TIER_CHANGED = "org.tier_changed"
RETENTION_CHANGED = "org.retention_changed"
SUSPENDED = "org.suspended"
UNSUSPENDED = "org.unsuspended"
DELETED = "org.deleted"


def inet(value: str | None) -> str | None:
    """An address the `inet` columns will accept, or nothing.

    Not private, because `auth.issue` needs it too: `platform_sessions.ip` is
    an `inet` as well, and a value Postgres refuses there fails the SIGN-IN,
    not merely the logging of it. A proxy header is attacker-controlled and a
    test client sends the literal string "testclient" — neither is an address,
    and neither is worth failing a request over.
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
    action: str,
    *,
    admin_id: str | None = None,
    org_id: str | None = None,
    target_type: str = "",
    target_id: str | None = None,
    reason: str = "",
    detail: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    s.execute(
        sa.insert(m.PlatformAction.__table__).values(
            id=f"pac_{secrets.token_hex(8)}",
            admin_id=admin_id,
            action=action,
            target_org_id=org_id,
            target_type=target_type,
            target_id=target_id,
            reason=reason,
            detail=detail or {},
            ip=inet(ip),
            user_agent=(user_agent or "")[:400] or None,
        )
    )


def record_in_its_own_transaction(action: str, **kw) -> None:
    """For a path that is about to raise — a failed sign-in, above all.

    Written inside the request's transaction, a failed-login row is rolled back
    by the 401 that follows it, so the one event a security review most wants
    is the one that never gets recorded. Same reasoning as
    `audit.record_even_if_the_request_fails`.
    """
    from .db import transaction

    try:
        with transaction() as s:
            record(s, action, **kw)
    except Exception:  # noqa: BLE001 - never mask the caller's own failure
        from ..logging import get_logger

        get_logger(__name__).warning("platform_action.write_failed", action=action)


def client_ip(request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
