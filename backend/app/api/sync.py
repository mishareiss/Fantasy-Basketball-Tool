"""Manual "sync now" endpoints. A scheduled job will call the same code paths later."""

from dataclasses import asdict
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.ages import NbaApiError, sync_ages
from app.db.session import get_db
from app.espn import sync_league
from app.espn.client import ESPNClient, ESPNCredentialsError, ESPNRequestError
from app.espn.lineups import pull_lineups
from app.scoring.settings import ESPNSettingsError
from app.team.calendar import UnknownSeasonError
from app.team.store import my_team

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

    # Last season's actual production, priced under OUR scoring and stored beside the
    # projections under its own `kind`. Counted separately because it is a separate pass over
    # the same payload — and because "we priced 431 completed seasons" is the line that tells
    # you the stat popup and the board's last-year column have something to show.
    actuals_seen: int = 0
    actuals_created: int = 0
    actuals_updated: int = 0
    actuals_unchanged: int = 0
    actuals_missing: int = 0
    actual_season: int | None = None

    adp_seen: int
    adp_season: int | None = None
    adp_created: int
    adp_updated: int
    adp_unchanged: int

    # This season's actual lines by kind (actual_current / actual_last_7 / _15 / _30), each
    # {seen, created, updated, unchanged, removed}; and whether any season-so-far row exists.
    current_actuals: dict[str, dict[str, int]] = {}
    actuals_available: bool = False

    # The NBA schedule, and how many games' Eastern dates disagree with their scoring period.
    games_seen: int = 0
    games_created: int = 0
    games_updated: int = 0
    games_unchanged: int = 0
    games_removed: int = 0
    schedule_date_mismatches: int = 0
    schedule_mismatched_games: list[str] = []

    teams_seen: int = 0
    my_team_id: int | None = None
    my_team_warning: str | None = None
    my_team_matchup_acquisition_totals: dict[str, Any] | None = None

    roster_entries: int = 0
    roster_entries_skipped: int = 0
    roster_scoring_period: int | None = None

    transactions_seen: int = 0
    transactions_created: int = 0
    transactions_updated: int = 0
    transactions_unchanged: int = 0

    # Keys of any WAIVERS entry in the pool, for spotting a real clear-time field.
    waiver_entry_keys: dict[str, list[str]] | None = None

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


def get_espn_client() -> ESPNClient:
    """The ESPN client for one request; a dependency so tests can hand in a stub."""
    try:
        return ESPNClient.from_settings()
    except ESPNCredentialsError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


class SyncLineupResponse(BaseModel):
    """What one lineup pull stored: the team, the days covered, and how many rows."""

    team_id: int
    first_sp: int
    last_sp: int
    days: int
    assignments: int


@router.post("/lineup", response_model=SyncLineupResponse)
def sync_lineup_now(
    db: Session = Depends(get_db),
    client: ESPNClient = Depends(get_espn_client),
    first_sp: int | None = Query(None, ge=1, description="First scoring period (default: today)"),
    last_sp: int | None = Query(
        None, ge=1, description="Last scoring period (default: end of next matchup)"
    ),
) -> SyncLineupResponse:
    """Pull our team's ESPN lineup for each day in range and store it.

    OVERWRITES every stored lineup row in those days, manual edits included. That is the
    point of the button. `POST /sync/league` never touches lineups.
    """
    team = my_team(db, client.season)
    if team is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No team is marked as ours for season {client.season}. Run the league sync "
            "(make sync) first, or set ESPN_MY_TEAM_ID.",
        )
    try:
        pulled = pull_lineups(db, client, team.team_id, first_sp, last_sp)
    except ESPNCredentialsError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    except ESPNRequestError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ESPN lineup pull failed: {exc}") from exc
    except UnknownSeasonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return SyncLineupResponse(**asdict(pulled))


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
