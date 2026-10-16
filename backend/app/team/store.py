"""Database lookups the Team tab shares."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import FantasyTeam


def my_team(db: Session, season: int) -> FantasyTeam | None:
    """Our fantasy team for `season`, as the last league sync resolved it. None if unresolved."""
    return db.scalar(
        select(FantasyTeam)
        .where(FantasyTeam.season == season, FantasyTeam.is_mine.is_(True))
        .order_by(FantasyTeam.team_id)
        .limit(1)
    )
