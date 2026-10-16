"""Database lookups the Team tab shares."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import FantasyTeam, LeagueSettings


def my_team(db: Session, season: int) -> FantasyTeam | None:
    """Our fantasy team for `season`, as the last league sync resolved it. None if unresolved."""
    return db.scalar(
        select(FantasyTeam)
        .where(FantasyTeam.season == season, FantasyTeam.is_mine.is_(True))
        .order_by(FantasyTeam.team_id)
        .limit(1)
    )


def league_settings_row(db: Session, season: int) -> LeagueSettings | None:
    """The stored settings for `season`: our configured league's row, else any (one query)."""
    rows = list(db.scalars(select(LeagueSettings).where(LeagueSettings.season == season)))
    league_id = get_settings().espn_league_id
    return next((r for r in rows if r.espn_league_id == league_id), rows[0] if rows else None)
