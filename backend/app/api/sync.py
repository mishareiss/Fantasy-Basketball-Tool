"""Manual "sync now" endpoint. A scheduled job will call the same code path later."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.espn import sync_league
from app.espn.client import ESPNCredentialsError, ESPNRequestError
from app.scoring.settings import ESPNSettingsError

router = APIRouter(prefix="/sync", tags=["sync"])


class SyncLeagueResponse(BaseModel):
    """Counts from one sync, plus the scoring formula we stored — meant to be eyeballed."""

    league_id: int
    season: int
    league_name: str | None = None
    scoring_type: str | None = None
    team_count: int | None = None

    scoring_rules: int
    scoring_rules_created: int
    scoring_rules_updated: int
    scoring_rules_removed: int

    players_seen: int
    players_created: int
    players_updated: int
    players_unchanged: int

    roster_slots: dict[str, int]
    points_by_stat: dict[str, float]


@router.post("/league", response_model=SyncLeagueResponse)
def sync_league_now(db: Session = Depends(get_db)) -> SyncLeagueResponse:
    """Pull scoring settings and the player pool from ESPN and upsert them. Idempotent."""
    try:
        summary = sync_league(db)
    except ESPNCredentialsError as exc:
        # 503, not 401: the caller isn't unauthorized — our server-side cookies are.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except (ESPNRequestError, ESPNSettingsError) as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ESPN sync failed: {exc}") from exc

    return SyncLeagueResponse(**asdict(summary))
