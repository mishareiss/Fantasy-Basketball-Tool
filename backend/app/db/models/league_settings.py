"""Our league's configuration as pulled from ESPN's `mSettings` view.

The scoring coefficients are the whole point: every projection we make is turned into
fantasy points with *these* numbers, not ESPN's defaults.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LeagueSettings(Base):
    """One row per (league, season). Re-syncing updates it in place."""

    __tablename__ = "league_settings"
    __table_args__ = (
        UniqueConstraint("espn_league_id", "season", name="uq_league_settings_league_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    espn_league_id: Mapped[int] = mapped_column(nullable=False)
    season: Mapped[int] = mapped_column(nullable=False)

    name: Mapped[str | None] = mapped_column(String(120))
    scoring_type: Mapped[str | None] = mapped_column(String(32))
    team_count: Mapped[int | None] = mapped_column()

    # ESPN's lineupSlotCounts, keyed by slot name ('PG', 'UT', 'BE', ...) rather than slot id.
    roster_slots: Mapped[dict | None] = mapped_column(JSON)

    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    scoring_rules: Mapped[list["ScoringRule"]] = relationship(
        back_populates="league_settings",
        cascade="all, delete-orphan",
        order_by="ScoringRule.stat_id",
    )

    def __repr__(self) -> str:
        return f"LeagueSettings(espn_league_id={self.espn_league_id!r}, season={self.season!r})"


class ScoringRule(Base):
    """One scored stat: `points` fantasy points per unit of `stat_name`.

    Stats ESPN does not score simply have no row — absence means zero, so unscored stats
    never contribute to a projection.
    """

    __tablename__ = "scoring_rule"
    __table_args__ = (
        UniqueConstraint("league_settings_id", "stat_id", name="uq_scoring_rule_settings_stat"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    league_settings_id: Mapped[int] = mapped_column(
        ForeignKey("league_settings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    stat_id: Mapped[int] = mapped_column(nullable=False)
    stat_name: Mapped[str] = mapped_column(String(16), nullable=False)
    points: Mapped[float] = mapped_column(Float, nullable=False)

    # ESPN's flag for "lower is better" items. Informational: the sign already lives in `points`.
    is_reverse: Mapped[bool] = mapped_column(nullable=False, default=False)

    league_settings: Mapped[LeagueSettings] = relationship(back_populates="scoring_rules")

    def __repr__(self) -> str:
        return f"ScoringRule(stat_name={self.stat_name!r}, points={self.points!r})"
