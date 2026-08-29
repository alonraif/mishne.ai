"""Sign up, sign in, sign out.

The interesting constraint here is that these are the only routes in the system
that run *before* a tenant is known. Everything else is handed an org by the
session; these have to establish one, and they do it through the two narrow
policy escapes migration 0003 adds — a lookup by session token, and a lookup by
the email being signed in with. Neither can read anything else.

**Signup creates the org.** A tier is chosen there (ADR-0006) and the person who
creates it is its owner. Nothing else in the system creates an organisation.

**SSO signs in a user who already exists.** A first sign-in from an identity
provider does not provision an account: an IdP asserts that someone controls an
email address, and turning that into membership of a customer's organisation is
a decision an owner makes. SCIM directory sync is the supported way to automate
it, and it is not built yet.
"""

from __future__ import annotations

import secrets

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import audit
from ..auth import passwords, sessions
from ..auth.providers import AuthError, LocalProvider, WorkOSProvider, get_provider
from ..auth.sessions import Principal
from ..config import Settings, get_settings
from ..db import models as m
from ..db.base import set_org
from ..deps import current_principal, unscoped_session
from ..logging import get_logger
from ..schemas import LoginRequest, Member, Org, Session, SignupRequest

router = APIRouter(prefix="/v1/auth", tags=["auth"])

log = get_logger(__name__)

#: The state cookie for a redirect sign-in. Short-lived, and the only thing that
#: makes the callback safe: a code delivered to it by anybody else is refused.
STATE_COOKIE = "mishne_sso_state"
STATE_TTL_SECONDS = 600


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        sessions.COOKIE_NAME,
        token,
        max_age=int(sessions.SESSION_TTL.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        # Lax rather than Strict: a link into the app from an email — which is
        # how an invitation arrives — must not land on a signed-out page. Lax
        # still withholds the cookie from cross-site POSTs, which is the case
        # that matters.
        samesite="lax",
        path="/",
    )


def _find_user_by_email(s: DbSession, email: str):
    """The one row a sign-in may see before it knows anything.

    `app.login_email` is the policy escape; without it this query returns
    nothing at all, which is what makes an unset tenant fail closed rather than
    scan every user in the system.
    """
    s.execute(
        sa.text("SELECT set_config('app.login_email', :e, true)"), {"e": email.lower()}
    )
    users = m.User.__table__
    return s.execute(
        sa.select(users).where(sa.func.lower(users.c.email) == email.lower())
    ).first()


def _credential_for(s: DbSession, org_id: str, user_id: str):
    s.execute(sa.text("SELECT set_config('app.login_user', :u, true)"), {"u": user_id})
    creds = m.UserCredential.__table__
    return s.execute(
        sa.select(creds).where(creds.c.org_id == org_id, creds.c.user_id == user_id)
    ).first()


def _session_body(s: DbSession, principal: Principal) -> Session:
    orgs = m.Org.__table__
    balances = m.OrgBalance.__table__
    row = s.execute(
        sa.select(orgs, balances.c.available, balances.c.held)
        .join(balances, balances.c.org_id == orgs.c.id, isouter=True)
        .where(orgs.c.id == principal.org_id)
    ).first()
    if row is None:  # pragma: no cover - the policy just let us read the session
        raise HTTPException(404, "organisation not found")
    return Session(
        user=Member(
            id=principal.user_id,
            email=principal.email,
            name=principal.name,
            role=principal.role,  # type: ignore[arg-type]
        ),
        org=Org(
            id=row.id,
            name=row.name,
            tier=row.tier,
            credit_balance=float(row.available or 0),
            credits_held=float(row.held or 0),
            retention_days=row.retention_days,
        ),
    )


@router.post("/signup", response_model=Session, status_code=201)
async def signup(
    body: SignupRequest,
    request: Request,
    response: Response,
    s: DbSession = Depends(unscoped_session),
    settings: Settings = Depends(get_settings),
) -> Session:
    """Create an organisation and its first owner.

    The org id is generated here and set on the transaction *before* the first
    insert, because every table's `WITH CHECK` requires a row to name the tenant
    it belongs to. There is no path that writes a row into a tenant the
    transaction is not already inside.
    """
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "that does not look like an email address")
    try:
        passwords.check_strength(body.password)
    except passwords.WeakPassword as exc:
        raise HTTPException(422, str(exc)) from exc
    if not body.org_name.strip():
        raise HTTPException(422, "an organisation needs a name")

    if _find_user_by_email(s, email) is not None:
        # Deliberately explicit rather than vague. This is a sign-up form: a
        # person who already has an account needs to be told to sign in, and the
        # address is one they typed themselves.
        raise HTTPException(409, "there is already an account with that email")

    org_id = f"org_{secrets.token_hex(4)}"
    user_id = f"usr_{secrets.token_hex(6)}"
    set_org(s, org_id)

    s.execute(
        sa.insert(m.Org.__table__).values(
            id=org_id,
            name=body.org_name.strip(),
            tier=body.tier,
            # The default retention for customer media. An owner can change it;
            # the lifecycle rules are the backstop (docs/architecture/04).
            retention_days=30,
        )
    )
    s.execute(sa.insert(m.OrgBalance.__table__).values(org_id=org_id))
    s.execute(
        sa.insert(m.User.__table__).values(
            id=user_id,
            org_id=org_id,
            email=email,
            name=body.name.strip(),
            role="owner",
            auth_provider="local",
        )
    )
    s.execute(
        sa.insert(m.UserCredential.__table__).values(
            id=f"crd_{secrets.token_hex(8)}",
            org_id=org_id,
            user_id=user_id,
            password_hash=passwords.hash_password(body.password),
        )
    )

    token = sessions.issue(s, org_id, user_id)
    audit.record(
        s, org_id, audit.SIGNUP, resource_type="org", resource_id=org_id,
        actor_user_id=user_id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings)
    log.info("signup", org_id=org_id, tier=body.tier)
    return _session_body(
        s,
        Principal(user_id=user_id, org_id=org_id, role="owner", session_id="",
                  email=email, name=body.name.strip()),
    )


@router.post("/login", response_model=Session)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    s: DbSession = Depends(unscoped_session),
    settings: Settings = Depends(get_settings),
) -> Session:
    """Email and password, against whichever provider the deployment runs.

    Every failure is the same failure. "No such account" and "wrong password"
    are different facts, and telling them apart turns a login form into a
    directory of who has an account here — which for a broadcaster is itself
    something they would rather not publish.
    """
    provider = get_provider(settings)
    if not provider.supports_password:
        raise HTTPException(400, "this deployment signs in through single sign-on")

    email = body.email.strip().lower()
    row = _find_user_by_email(s, email)
    if row is None:
        # No org to attribute an audit row to, and inventing one would put a
        # stranger's typo in a customer's log. The metric is the log line.
        log.info("login_failed", reason="unknown_email")
        raise HTTPException(401, "that email and password do not match")

    set_org(s, row.org_id)
    credential = _credential_for(s, row.org_id, row.id)
    try:
        if credential is None:
            raise AuthError("that email and password do not match")
        assert isinstance(provider, LocalProvider)
        provider.verify_hash(email, body.password, credential.password_hash)
    except AuthError:
        # In its own transaction: this request is about to raise a 401, and the
        # rollback would otherwise take the audit row with it — losing exactly
        # the event a security review asks about.
        audit.record_even_if_the_request_fails(
            row.org_id, audit.LOGIN_FAILED, resource_type="user",
            resource_id=row.id, ip=audit.client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(401, "that email and password do not match") from None

    if passwords.needs_rehash(credential.password_hash):
        # The one moment the plaintext exists. Raising the cost parameters later
        # is otherwise a change nobody can ever apply.
        creds = m.UserCredential.__table__
        s.execute(
            sa.update(creds)
            .where(creds.c.org_id == row.org_id, creds.c.user_id == row.id)
            .values(password_hash=passwords.hash_password(body.password))
        )

    token = sessions.issue(s, row.org_id, row.id)
    audit.record(
        s, row.org_id, audit.LOGIN, resource_type="user", resource_id=row.id,
        actor_user_id=row.id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings)
    return _session_body(
        s,
        Principal(user_id=row.id, org_id=row.org_id, role=row.role, session_id="",
                  email=row.email, name=row.name),
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    principal: Principal = Depends(current_principal),
    s: DbSession = Depends(unscoped_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Revoke this session. The cookie is cleared whether or not that succeeds."""
    set_org(s, principal.org_id)
    sessions.revoke(s, principal.org_id, principal.session_id)
    audit.record(
        s, principal.org_id, audit.LOGOUT, resource_type="session",
        resource_id=principal.session_id, actor_user_id=principal.user_id,
        ip=audit.client_ip(request), user_agent=request.headers.get("user-agent"),
    )
    response.delete_cookie(
        sessions.COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure,
        samesite="lax",
    )
    return Response(status_code=204, headers=dict(response.headers))


@router.get("/me", response_model=Session)
async def me(
    principal: Principal = Depends(current_principal),
    s: DbSession = Depends(unscoped_session),
) -> Session:
    """Who the caller is. What the web app asks on every page load."""
    set_org(s, principal.org_id)
    return _session_body(s, principal)


# ──────────────────────────────────────────────────────────── single sign-on


@router.get("/sso/start")
async def sso_start(
    response: Response, settings: Settings = Depends(get_settings)
) -> dict:
    """Where to send the browser, and the state that proves it came back.

    Returns the URL rather than issuing a redirect: the caller is a fetch from
    the web app, and a 302 to another origin inside a fetch is a worse failure
    to debug than a URL the app can navigate to itself.
    """
    provider = WorkOSProvider(settings)
    state = secrets.token_urlsafe(24)
    try:
        url = provider.authorization_url(state, f"{settings.app_origin}/login/callback")
    except AuthError as exc:
        raise HTTPException(501, str(exc)) from exc
    response.set_cookie(
        STATE_COOKIE, state, max_age=STATE_TTL_SECONDS, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return {"url": url}


@router.get("/sso/callback", response_model=Session)
async def sso_callback(
    code: str,
    state: str,
    request: Request,
    response: Response,
    s: DbSession = Depends(unscoped_session),
    settings: Settings = Depends(get_settings),
) -> Session:
    expected = request.cookies.get(STATE_COOKIE, "")
    if not expected or not secrets.compare_digest(expected, state):
        raise HTTPException(400, "that sign-in did not start here; try again")

    try:
        identity = WorkOSProvider(settings).complete(
            code, f"{settings.app_origin}/login/callback"
        )
    except AuthError as exc:
        raise HTTPException(401, str(exc)) from exc

    row = _find_user_by_email(s, identity.email)
    if row is None:
        # An identity provider asserts that someone controls an email address.
        # Turning that into membership of a customer's organisation is a
        # decision an owner makes — SCIM is the supported way to automate it.
        raise HTTPException(403, "no account here yet; ask an owner to invite you")

    set_org(s, row.org_id)
    users = m.User.__table__
    s.execute(
        sa.update(users)
        .where(users.c.org_id == row.org_id, users.c.id == row.id)
        .values(auth_provider="workos", external_id=identity.external_id or row.external_id)
    )
    token = sessions.issue(s, row.org_id, row.id)
    audit.record(
        s, row.org_id, audit.LOGIN, resource_type="user", resource_id=row.id,
        actor_user_id=row.id, ip=audit.client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token, settings)
    response.delete_cookie(STATE_COOKIE, path="/")
    return _session_body(
        s,
        Principal(user_id=row.id, org_id=row.org_id, role=row.role, session_id="",
                  email=row.email, name=row.name),
    )
