"""Where an identity comes from, behind one interface.

The same shape as ASR (ADR-0003) and LLMs (ADR-0011), for the same reason: the
provider is a commercial decision that changes, and the rest of the system
should not know which one is in use.

Two implementations:

* **`LocalProvider`** — email and password, in our own database. It is what a
  developer's machine and the test suite run on, so neither needs a vendor
  account, a network, or a browser redirect to sign in. It is also a real
  product path: a single-editor customer who will never do SSO can use it.
* **`WorkOSProvider`** — the hosted identity provider
  `docs/architecture/04-security.md` names. SAML SSO and SCIM directory sync are
  asked for in the first procurement conversation with any broadcast buyer, and
  building them is weeks of work with a long tail of provider-specific quirks.

What the interface deliberately does NOT do: create users, create orgs, or issue
sessions. A provider answers one question — *who is this?* — and returns an
`ExternalIdentity`. Everything downstream of that answer is ours, which is what
keeps a provider swap from touching the tenancy model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..config import Settings, get_settings


@dataclass(frozen=True)
class ExternalIdentity:
    """Who the provider says this is. Never a role, never an org — those are ours."""

    email: str
    #: The provider's own stable id for this person. Empty for the local
    #: provider, where our user id is the only id there is.
    external_id: str = ""
    name: str = ""
    provider: str = "local"
    #: The provider's organisation id, when it has one. WorkOS returns it for an
    #: SSO connection, and it is how a second person from the same company lands
    #: in the same org rather than creating a new one.
    external_org_id: str = ""


class AuthError(Exception):
    """Authentication did not succeed. The message is safe to show a user."""


@runtime_checkable
class AuthProvider(Protocol):
    name: str
    #: Whether this provider authenticates a password directly. False for any
    #: redirect-based provider, where the API never sees a credential at all.
    supports_password: bool

    def authenticate(self, email: str, password: str) -> ExternalIdentity:
        """Verify a password. Raises `AuthError` if it does not."""

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        """Where to send the browser to start a redirect sign-in."""

    def complete(self, code: str, redirect_uri: str) -> ExternalIdentity:
        """Exchange the code the provider redirected back with."""


class LocalProvider:
    """Email and password, verified against `user_credentials`.

    The verification itself is not here: this provider is handed an already-read
    hash by the login route, because reading it means a query, and a query means
    a transaction with the right session variables set. Keeping SQL out of the
    provider is what lets a second implementation exist at all.
    """

    name = "local"
    supports_password = True

    def authenticate(self, email: str, password: str) -> ExternalIdentity:
        # Present for interface completeness; the route calls `verify_hash`.
        raise NotImplementedError(
            "the local provider verifies against a hash the caller has read"
        )

    def verify_hash(self, email: str, password: str, encoded: str) -> ExternalIdentity:
        from . import passwords

        if not passwords.verify(password, encoded):
            raise AuthError("that email and password do not match")
        return ExternalIdentity(email=email.lower(), provider=self.name)

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        raise AuthError("the local provider has no redirect flow")

    def complete(self, code: str, redirect_uri: str) -> ExternalIdentity:
        raise AuthError("the local provider has no redirect flow")


class WorkOSProvider:
    """SSO through WorkOS: the browser goes there, and comes back with a code.

    Nothing here holds a password, which is the point of buying it: the SAML
    quirks of every customer's identity provider are somebody else's problem,
    and a broadcast buyer's security review gets the answer it is looking for.

    Unconfigured, every method raises with the setting that is missing rather
    than failing at an HTTP call with a 401 from a vendor.
    """

    name = "workos"
    supports_password = False

    AUTHORIZE_URL = "https://api.workos.com/sso/authorize"
    TOKEN_URL = "https://api.workos.com/sso/token"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def _require(self) -> tuple[str, str]:
        key = self.settings.workos_api_key
        client = self.settings.workos_client_id
        if not key or not client:
            raise AuthError(
                "single sign-on is not configured: WORKOS_API_KEY and "
                "WORKOS_CLIENT_ID are unset"
            )
        return key, client

    def authenticate(self, email: str, password: str) -> ExternalIdentity:
        raise AuthError("this organisation signs in through your identity provider")

    def authorization_url(self, state: str, redirect_uri: str) -> str:
        from urllib.parse import urlencode

        _key, client = self._require()
        query = {
            "client_id": client,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            # `state` is the CSRF defence for the whole flow: it is signed by us
            # and checked on the way back, so a code delivered to the callback
            # by anybody else is refused.
            "state": state,
        }
        if self.settings.workos_connection_id:
            query["connection"] = self.settings.workos_connection_id
        elif self.settings.workos_organization_id:
            query["organization"] = self.settings.workos_organization_id
        else:
            query["provider"] = "authkit"
        return f"{self.AUTHORIZE_URL}?{urlencode(query)}"

    def complete(self, code: str, redirect_uri: str) -> ExternalIdentity:
        import httpx

        key, client = self._require()
        try:
            response = httpx.post(
                self.TOKEN_URL,
                data={
                    "client_id": client,
                    "client_secret": key,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001 - network shapes vary
            raise AuthError("could not reach the identity provider") from exc
        if response.status_code >= 400:
            # Never surface the vendor's body: it echoes the code, which is a
            # credential for the duration of the exchange.
            raise AuthError("the identity provider rejected the sign-in")

        profile = response.json().get("profile", {})
        email = (profile.get("email") or "").lower()
        if not email:
            raise AuthError("the identity provider returned no email address")
        return ExternalIdentity(
            email=email,
            external_id=profile.get("id", ""),
            name=" ".join(
                part for part in (profile.get("first_name"), profile.get("last_name")) if part
            ),
            provider=self.name,
            external_org_id=profile.get("organization_id", "") or "",
        )


def get_provider(settings: Settings | None = None) -> AuthProvider:
    settings = settings or get_settings()
    if settings.auth_provider == "workos":
        return WorkOSProvider(settings)
    return LocalProvider()
