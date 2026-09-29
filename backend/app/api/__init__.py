"""HTTP routers. Feature routers (players, draft, rankings, ingest, ...) get mounted here."""

from fastapi import APIRouter, Depends

from app.api import (
    consensus,
    draft,
    health,
    imports,
    market,
    master,
    players,
    rankings,
    sync,
    valuation,
)
from app.auth import require_access_token

# The shared-password gate hangs here, on the one router everything else is mounted under, so
# a feature router added below is behind it by construction. It is a pass-through unless
# APP_ACCESS_TOKEN is set, and the health endpoints are exempt at any setting (app/auth.py).
api_router = APIRouter(dependencies=[Depends(require_access_token)])
api_router.include_router(health.router)
api_router.include_router(players.router)
api_router.include_router(sync.router)
api_router.include_router(imports.router)
api_router.include_router(rankings.router)
api_router.include_router(valuation.router)
api_router.include_router(consensus.router)
api_router.include_router(market.router)
api_router.include_router(master.router)
api_router.include_router(draft.router)
