"""HTTP routers. Feature routers (players, draft, rankings, ingest, ...) get mounted here."""

from fastapi import APIRouter

from app.api import health

api_router = APIRouter()
api_router.include_router(health.router)
