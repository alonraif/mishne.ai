from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging import configure as configure_logging, get_logger
from .routers import assets, auth, billing, jobs, org, projects

configure_logging()
settings = get_settings()
log = get_logger(__name__)

app = FastAPI(
    title="mishne.ai",
    version="0.0.0",
    description="Raw footage to editable rough cut.",
)

# Credentialed CORS cannot use a wildcard — the browser refuses it — which is
# the correct outcome: the origins that may drive this API with a session cookie
# are named, per environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.app_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Response headers a cross-origin caller is allowed to read. Without this a
    # browser can see the status and the body and nothing else, so the id
    # `assets.create_asset` returns on a 409 — the identity of the file already
    # uploaded, which is what turns that refusal into an answer — was
    # unreadable from the app that needed it.
    expose_headers=["X-Asset-Id"],
)

@app.middleware("http")
async def explain_a_refused_origin(request, call_next):
    """Say, once, when a browser is turned away for its origin.

    Credentialed CORS names the origins that may drive this API, and a request
    from any other one is refused by the browser with "No
    'Access-Control-Allow-Origin' header is present" — an error about a missing
    header, in the browser console, that says nothing about which origin was
    expected or where that expectation is configured.

    The commonest cause is not an attack: it is the dev server finding port 3000
    busy and quietly starting on 3001. So the API says what it was expecting,
    on the server side, where the person running it is already looking.
    """
    origin = request.headers.get("origin")
    if origin and origin != settings.app_origin:
        log.warning(
            "cors.refused",
            origin=origin,
            expected=settings.app_origin,
            hint="set APP_ORIGIN to the origin the app is actually served from",
        )
    return await call_next(request)


app.include_router(auth.router)
app.include_router(org.router)
app.include_router(projects.router)
app.include_router(assets.router)
app.include_router(jobs.router)
app.include_router(billing.router)


@app.get("/", tags=["meta"])
async def root() -> dict:
    """Where somebody who opened the base URL should go next.

    This is an API and the app is served from somewhere else, so `/` has
    nothing to render — but the first thing a person does with a new service is
    open its address, and answering that with a 404 and a stack trace in the
    log reads as a broken server rather than as a correct one.
    """
    return {
        "service": "mishne.ai",
        "docs": "/docs",
        "health": "/health",
        "app": settings.app_origin,
    }


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "mocks": settings.use_mocks}
