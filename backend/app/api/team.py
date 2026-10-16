"""The in-season Team tab: status, the day view, lineup edits, the waiver list, the player card
and the calendar. Thin: the work is in `app.team.views`."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LineupAssignment, NbaGame, RosterEntry
from app.db.session import get_db
from app.team import views
from app.team.calendar import (
    UnknownSeasonError,
    effective_period,
    local_today,
    matchup_for_period,
    today_period,
)
from app.team.schemas import (
    Basis,
    FreeAgentsResponse,
    LineupEditRequest,
    LineupEditResponse,
    MatchupRef,
    PlayerCardResponse,
    TeamCalendarResponse,
    TeamDayResponse,
    TeamRef,
)
from app.team.store import my_team
from app.team.waivers import acquisition_settings

router = APIRouter(prefix="/team", tags=["team"])


def _now() -> datetime:
    """The clock, as a seam the tests can pin."""
    return datetime.now(UTC)


class TeamStatusResponse(BaseModel):
    """What the Team tab has to work with, and what day the app thinks it is."""

    season: int
    my_team: TeamRef | None = None
    # Today's fantasy scoring period (Pacific date), or null before/after the fantasy season.
    today_period: int | None = None
    today_date: date
    # The matchup containing today; before tip-off the first one, after the final the last.
    current_matchup: MatchupRef
    games_synced: int
    roster_size: int
    lineup_days_stored: int
    actuals_available: bool
    acquisition_limit: int | None = None


@router.get("/status", response_model=TeamStatusResponse)
def team_status(db: Session = Depends(get_db)) -> TeamStatusResponse:
    """Season, our team, today's period and matchup, and how much in-season data is stored."""
    season = get_settings().espn_season
    if season is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "ESPN_SEASON is not configured")

    now = _now()
    try:
        period = today_period(season, now)
        matchup = matchup_for_period(season, effective_period(season, now))
    except UnknownSeasonError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    assert matchup is not None  # the effective period is always inside the calendar

    team = my_team(db, season)
    roster_size = lineup_days = 0
    if team is not None:
        roster_size = db.scalar(
            select(func.count())
            .select_from(RosterEntry)
            .where(RosterEntry.season == season, RosterEntry.team_id == team.team_id)
        )
        lineup_days = db.scalar(
            select(func.count(func.distinct(LineupAssignment.scoring_period_id))).where(
                LineupAssignment.season == season, LineupAssignment.team_id == team.team_id
            )
        )

    games = db.scalar(select(func.count()).select_from(NbaGame).where(NbaGame.season == season))
    return TeamStatusResponse(
        season=season,
        my_team=(TeamRef(id=team.team_id, name=team.name, abbrev=team.abbrev) if team else None),
        today_period=period,
        today_date=local_today(now),
        current_matchup=MatchupRef(
            id=matchup.id, label=matchup.label, start=matchup.start, end=matchup.end
        ),
        games_synced=games or 0,
        roster_size=roster_size or 0,
        lineup_days_stored=lineup_days or 0,
        actuals_available=views.actuals_available(db, season),
        acquisition_limit=acquisition_settings(db, season).matchup_acquisition_limit,
    )


# --- the Team tab's views -------------------------------------------------------------------


def _season() -> int:
    season = get_settings().espn_season
    if season is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "ESPN_SEASON is not configured")
    return season


@contextmanager
def _domain_errors() -> Iterator[None]:
    """The views' errors as HTTP: not-synced-yet is a 409, a bad day or lineup a 422."""
    try:
        yield
    except (views.TeamNotReady, UnknownSeasonError) as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except views.PlayerNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except views.LineupRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors) from exc
    except views.PeriodOutOfRange as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


def _default_period(season: int, sp: int | None) -> int:
    return effective_period(season, _now()) if sp is None else sp


@router.get("/day", response_model=TeamDayResponse)
def team_day(
    sp: int | None = Query(None, description="Scoring period (default: today, clamped)"),
    basis: Basis = Query("projected", description="projected, or this season's season/l7/l15/l30"),
    db: Session = Depends(get_db),
) -> TeamDayResponse:
    """My roster on one day, in ESPN's slot grid (empty slots included), with each player's
    game that day and his stat line under `basis`."""
    season = _season()
    with _domain_errors():
        return views.team_day(db, season, _default_period(season, sp), basis, _now())


@router.put("/lineup/{sp}", response_model=LineupEditResponse)
def edit_lineup(
    sp: int = Path(..., description="The first day to set"),
    body: LineupEditRequest = Body(...),
    basis: Basis = Query("projected"),
    db: Session = Depends(get_db),
) -> LineupEditResponse:
    """Set my lineup by hand for day `sp` (and every day through `through_sp`).

    Every rostered player exactly once, each in a slot he's eligible for and with room. Any
    error is a 422 listing all of them, and nothing is written. Stays local: only
    `POST /sync/lineup` replaces it with ESPN's lineup.
    """
    season = _season()
    assignments = [(a.player_id, a.slot_id) for a in body.assignments]
    with _domain_errors():
        return views.edit_lineup(db, season, sp, assignments, body.through_sp, basis, _now())


def _parse_days(raw: str | None, season: int) -> list[int]:
    if raw is None:
        return [effective_period(season, _now())]
    try:
        days = [int(part) for part in raw.split(",") if part.strip()]
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"days must be a comma-separated list of scoring periods, not {raw!r}",
        ) from exc
    return list(dict.fromkeys(days))


@router.get("/free-agents", response_model=FreeAgentsResponse)
def free_agents(
    days: str | None = Query(None, description="Comma-separated scoring periods (default: today)"),
    mode: Literal["none", "any", "all"] = Query(
        "none", description="none: no day filter; any: plays on one of `days`; all: on every one"
    ),
    basis: Basis = Query("projected"),
    position: Literal["PG", "SG", "SF", "PF", "C"] | None = Query(None),
    status_: Literal["all", "FREEAGENT", "WAIVERS"] = Query("all", alias="status"),
    q: str | None = Query(None, description="Name contains (case-insensitive)"),
    sort: Literal["fpts", "name", "games"] = Query("fpts"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    include_unsigned: bool = Query(False, description="Include players with no NBA team"),
    db: Session = Depends(get_db),
) -> FreeAgentsResponse:
    """Everyone on no roster (free agents and waivers), with their games on `days`."""
    season = _season()
    parsed = _parse_days(days, season)
    with _domain_errors():
        return views.free_agents(
            db,
            season,
            days=parsed,
            mode=mode,
            basis=basis,
            position=position,
            status=status_,
            q=q,
            sort=sort,
            limit=limit,
            offset=offset,
            include_unsigned=include_unsigned,
        )


@router.get("/player/{player_id}", response_model=PlayerCardResponse)
def player_card(
    player_id: int = Path(..., description="Our canonical (ESPN) player id"),
    from_sp: int | None = Query(None, description="First day of the 7-day window (default: today)"),
    db: Session = Depends(get_db),
) -> PlayerCardResponse:
    """One player's card: every stat basis, who holds him, and his next 7 days by matchup."""
    season = _season()
    with _domain_errors():
        return views.player_card(db, season, player_id, _default_period(season, from_sp))


@router.get("/calendar", response_model=TeamCalendarResponse)
def team_calendar(db: Session = Depends(get_db)) -> TeamCalendarResponse:
    """Everything the date picker needs: every day, its matchup, and how many of mine play."""
    season = _season()
    with _domain_errors():
        return views.team_calendar(db, season, _now())
