"""Manual "sync now" endpoints. A scheduled job will call the same code paths later."""

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ages import NbaApiError, sync_ages
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

    projections_seen: int
    projections_created: int
    projections_updated: int
    projections_unchanged: int
    projections_missing: int
    projection_season: int | None = None

    adp_seen: int
    adp_season: int | None = None
    adp_created: int
    adp_updated: int
    adp_unchanged: int

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


class SyncAgesResponse(BaseModel):
    """Counts from one age sync, plus the worklist of players it couldn't resolve."""

    # Every age below was computed at this date, not at "now" — see Settings.age_as_of.
    age_as_of: date

    nba_roster: int
    nba_matched: int
    nba_ambiguous: int
    nba_unmatched: int
    aliases_created: int
    aliases_existing: int

    players_total: int
    players_with_alias: int
    players_without_alias: int
    birthdates_fetched: int
    birthdates_absent: int
    birthdates_failed: int
    birthdates_pending: int
    ages_set: int
    players_with_age: int
    players_missing_age: int

    unresolved_players: list[str]
    ambiguous_names: list[str]


@router.post("/ages", response_model=SyncAgesResponse)
def sync_ages_now(
    db: Session = Depends(get_db),
    limit: int | None = Query(
        None, ge=1, description="Stop after this many birthdate fetches (best players first)"
    ),
    refresh: bool = Query(False, description="Re-fetch birthdates we already have"),
) -> SyncAgesResponse:
    """Match nba.com's roster to our players, fetch missing birthdates, recompute ages.

    Idempotent and resumable. Note this is one HTTP call to nba.com per player still missing a
    birthdate, paced politely — so the *first* full run takes minutes and is better done from
    the CLI (`make sync-ages`). Use `limit` to keep an API call short.
    """
    try:
        summary = sync_ages(db, limit=limit, refresh=refresh)
    except NbaApiError as exc:
        # 502, not 500: nba.com is the thing that failed, and it's the thing to retry.
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"nba.com refused us: {exc}") from exc

    return SyncAgesResponse(**asdict(summary))
