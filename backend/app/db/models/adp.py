"""Average draft position from one source, per player, per season.

Separate from `Projection` because ADP is a *market* signal, not a production estimate: it
says what a room does, and our whole edge is the gap between that and what a player is worth
under our scoring. One row per (player, source, season) so more sources stack up as they
arrive — and so a season's ADP is never overwritten by the next one's.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player


class AdpEntry(Base):
    """One source's draft-market read on one player, in one season."""

    __tablename__ = "adp_entry"
    __table_args__ = (
        UniqueConstraint("player_id", "source", "season", name="uq_adp_entry_player_source_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 'espn' today; imported consensus sources land alongside it.
    source: Mapped[str] = mapped_column(String(24), nullable=False)

    # The season this ADP is FOR, labelled the way `Projection.season` is (the year the season
    # ends: 2027 is 2026-27). Part of the key rather than a detail, because dynasty value is a
    # trend: "where did the room have him last year vs this year" is a signal in its own right,
    # and a table keyed on (player, source) alone destroys last year's answer every August.
    # Never nullable — an ADP without a season is a number nobody can interpret later.
    season: Mapped[int] = mapped_column(nullable=False)

    # ESPN's numbers are REDRAFT and use ESPN's default scoring, not ours — stored raw, with
    # the dynasty and custom-scoring adjustments applied downstream where they're tunable.
    # ESPN floors ADP at roughly the last pick of a standard draft, so an undrafted player
    # comes back at ~140 rather than null.
    adp: Mapped[float | None] = mapped_column(Float)
    auction_value: Mapped[float | None] = mapped_column(Float)
    percent_owned: Mapped[float | None] = mapped_column(Float)

    # When these values last changed, not when we last looked.
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="adp_entries")

    def __repr__(self) -> str:
        return (
            f"AdpEntry(player_id={self.player_id!r}, source={self.source!r}, "
            f"season={self.season!r}, adp={self.adp!r})"
        )
