"""Identity: who a request is, and which tenant that puts it in.

`providers` answers *who is this?* behind one interface — email and password
locally, WorkOS in production. `sessions` turns that answer into a cookie and
back, and is where `app.org_id` gets set for a request. `passwords` is the
hashing the local provider needs.
"""

from .providers import AuthError, AuthProvider, ExternalIdentity, get_provider
from .sessions import COOKIE_NAME, Principal

__all__ = [
    "COOKIE_NAME",
    "AuthError",
    "AuthProvider",
    "ExternalIdentity",
    "Principal",
    "get_provider",
]
