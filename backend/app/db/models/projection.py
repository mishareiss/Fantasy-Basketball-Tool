"""Projected production for a player, priced under our custom scoring.

One row per (player, source, kind, season), so ESPN's projection, an imported CSV, and a
market-implied line coexist for the same player and stay individually inspectable. The board
picks which source it trusts; nothing is blended at write time.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player


class Projection(Base):
    """A full-season projection and the fantasy points it is worth to us."""

    __tablename__ = "projection"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "source",
            "kind",
            "season",
            name="uq_projection_player_source_kind_season",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Where the numbers came from ('espn' today; 'fantasypros', 'market', 'model' later).
    source: Mapped[str] = mapped_column(String(24), nullable=False)
    # What kind of projection it is — 'projected_season' today; rest-of-season and per-game
    # horizons get their own kinds rather than their own tables.
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # The season the projection is FOR, as the source labels it. Not necessarily the season we
    # asked for: out of season ESPN's newest projection is still the previous one, and storing
    # what it actually is keeps that honest.
    season: Mapped[int] = mapped_column(nullable=False)

    # Season totals, keyed by stat name ('PTS', 'REB', ...). Counting stats only — never
    # percentages — so anything in here is safe to multiply by a coefficient.
    raw_stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # The same stats per game, as the source reported them. Kept because a rate is what the
    # board displays and what per-game valuation works from.
    per_game_stats: Mapped[dict | None] = mapped_column(JSON)

    projected_games: Mapped[float | None] = mapped_column(Float)

    fantasy_points_total: Mapped[float] = mapped_column(Float, nullable=False)
    fantasy_points_per_game: Mapped[float] = mapped_column(Float, nullable=False)
    # 'projected_games' | 'per_game_stats' | 'unavailable' — see app.scoring.projections.
    per_game_basis: Mapped[str] = mapped_column(String(24), nullable=False)

    # The source's own fantasy points for this projection, where it publishes them. ESPN
    # applies our custom coefficients server-side, so a gap between this and
    # `fantasy_points_total` means our scoring load is wrong.
    source_fantasy_points_total: Mapped[float | None] = mapped_column(Float)

    # When these values last changed, not when we last looked — an unchanged re-sync leaves it
    # alone, so it reads as "ESPN moved this projection on...".
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="projections")

    def __repr__(self) -> str:
        return (
            f"Projection(player_id={self.player_id!r}, source={self.source!r}, "
            f"season={self.season!r}, fppg={self.fantasy_points_per_game:.1f})"
        )
