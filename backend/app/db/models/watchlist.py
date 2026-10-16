"""The waiver watchlist: players we're keeping an eye on, shared by both co-managers.

In the database rather than the browser so both of us see the same list. Keyed by season, and
kept when a watched player gets rostered: he drops out of the free-agent list on his own, and
the entry is still there if he's dropped again.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime


class WatchlistEntry(Base):
    """One watched player for one season."""

    __tablename__ = "watchlist_entry"
    __table_args__ = (
        UniqueConstraint("season", "player_id", name="uq_watchlist_entry_season_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500))

    def __repr__(self) -> str:
        return f"WatchlistEntry(season={self.season!r}, player_id={self.player_id!r})"
