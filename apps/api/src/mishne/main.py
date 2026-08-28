from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging import configure as configure_logging
from .routers import assets, billing, jobs, projects

configure_logging()
settings = get_settings()

app = FastAPI(
    title="mishne.ai",
    version="0.0.0",
    description="Raw footage to editable rough cut.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router)
app.include_router(assets.router)
app.include_router(jobs.router)
app.include_router(billing.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "mocks": settings.use_mocks}
