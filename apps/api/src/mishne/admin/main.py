"""The back-office application. Run it separately, and never on a public port.

    uvicorn mishne.admin.main:app --host 127.0.0.1 --port 8001

Two things are checked before it will serve, both at startup, both because the
failure they prevent is silent rather than loud.

**It must actually bypass RLS.** A connection without that exemption sees no
rows through any of these queries, so the back-office would come up, sign you
in, and show no organisations — which reads as "no customers" and not as
"pointed at the wrong role".

**It must not be reachable from the internet.** The customer API is behind
authentication because it has to be on the internet; this process has no such
requirement, so the cheapest control available is the right one. Binding
anywhere but loopback needs `ADMIN_ALLOW_PUBLIC_BIND=true`, which is a thing
somebody has to decide rather than a `--host 0.0.0.0` copied from the API's
command line.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..config import get_settings, load_env_file
from ..logging import configure as configure_logging, get_logger
from . import db as admin_db
from .routes import router

load_env_file()
configure_logging()
settings = get_settings()
log = get_logger(__name__)


class Misconfigured(RuntimeError):
    """Refuse to serve rather than serve wrongly."""


def _check_bind() -> None:
    """Whatever uvicorn was told to bind to, as far as this process can see it.

    Read from the environment rather than from uvicorn's config because there
    is no supported way to ask the running server, and because the deployment
    that gets this wrong is the one where the host came from a variable anyway.
    This is a guard rail, not a security boundary: the boundary is the network
    the process is on.
    """
    host = os.environ.get("ADMIN_HOST") or os.environ.get("HOST") or "127.0.0.1"
    if host in ("127.0.0.1", "::1", "localhost", ""):
        return
    if not settings.admin_allow_public_bind:
        raise Misconfigured(
            f"the back-office was asked to bind to {host}. Put it behind a VPN or "
            "an SSH tunnel and bind loopback, or set ADMIN_ALLOW_PUBLIC_BIND=true "
            "if something in front of it is doing the access control."
        )
    log.warning("admin.public_bind", host=host)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _check_bind()
    if not admin_db.bypasses_rls():
        raise Misconfigured(
            f"connected as {admin_db.connected_as()!r}, which is subject to "
            "row-level security. The back-office would show no organisations at "
            "all. Point ADMIN_DATABASE_URL at a role created with BYPASSRLS — "
            "see migrations/versions/0009_platform_administration.py."
        )
    log.info(
        "admin.ready",
        connected_as=admin_db.connected_as(),
        environment=settings.environment,
    )
    yield


app = FastAPI(
    lifespan=lifespan,
    title="mishne.ai back-office",
    version="0.0.0",
    description="Platform administration. Not a customer-facing API.",
    # No published schema outside a developer's machine. This service's shape
    # is not something to advertise, and /docs on an admin surface is a menu
    # for anyone who reaches it.
    openapi_url="/admin/v1/openapi.json" if settings.environment == "local" else None,
    docs_url="/admin/v1/docs" if settings.environment == "local" else None,
    redoc_url=None,
)

# Its own origin, and only its own. Nothing about this list has anything to do
# with `app_origin`: the product's app has no business calling this API, and
# saying so here means a mistake in the product cannot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.admin_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "service": "back-office"}
