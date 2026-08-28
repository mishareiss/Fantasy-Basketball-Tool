"""HTTP routers. Feature routers (players, draft, rankings, ingest, ...) get mounted here."""

from fastapi import APIRouter

from app.api import health, imports, players, sync

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(players.router)
api_router.include_router(sync.router)
api_router.include_router(imports.router)
