"""The in-season Team tab. One endpoint for now: is the data behind it in place?"""

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LineupAssignment, NbaGame, Projection, RosterEntry
from app.db.session import get_db
from app.espn.sync import ACTUAL_CURRENT_KIND, ESPN_SOURCE
from app.team.calendar import (
    UnknownSeasonError,
    effective_period,
    local_today,
    matchup_for_period,
    today_period,
)
from app.team.store import my_team
from app.team.waivers import acquisition_settings

router = APIRouter(prefix="/team", tags=["team"])


def _now() -> datetime:
    """The clock, as a seam the tests can pin."""
    return datetime.now(UTC)


class TeamRef(BaseModel):
    id: int
    name: str
    abbrev: str | None = None


class MatchupRef(BaseModel):
    id: int
    label: str
    start: date
    end: date


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
    actuals = (
        db.scalar(
            select(Projection.id)
            .where(
                Projection.source == ESPN_SOURCE,
                Projection.kind == ACTUAL_CURRENT_KIND,
                Projection.season == season,
            )
            .limit(1)
        )
        is not None
    )

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
        actuals_available=actuals,
        acquisition_limit=acquisition_settings(db, season).matchup_acquisition_limit,
    )
