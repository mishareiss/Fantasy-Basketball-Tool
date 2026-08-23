"""FastAPI entrypoint: builds the app, mounts routers, and configures CORS for the frontend."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import api_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="Fantasy Basketball Dynasty Tool",
    description="Backend API for draft prep, valuation, and league analysis.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/", tags=["meta"])
def root() -> dict[str, str]:
    """Basic service info, handy for confirming which build is running."""
    return {"name": app.title, "version": app.version, "docs": "/docs"}
