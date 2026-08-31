from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging import configure as configure_logging
from .routers import assets, auth, billing, jobs, org, projects

configure_logging()
settings = get_settings()

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
)

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
