"""One sportsbook line on one stat for one player, in one season.

The third source on the consensus board (FEATURE_SPEC 5), and the only one that is entered by
hand: season-long props are published as web pages, not as a feed, so a line arrives as
"Nikola Jokic, assists, 9.5, over -135, under +110" typed or pasted in.

Stored as a row per STAT rather than a row per player, which is the whole shape of the thing:

* **A line moves on its own.** An assists line reprices in September without the points line
  moving, and `(source, season, player, stat)` being the key is what lets one stat be updated
  in place — "update the odds if they change" — without restating the other four.
* **A player's lines are a SET, and the set is partial.** Books price the stats they price.
  Nothing here pretends to be a full projection; the projection is DERIVED from whatever
  stats exist (`app.ingest.market_line`), so a player with only a points line gets a
  market projection built from points alone. That is honest rather than complete, and it is
  the reason a market source should be read next to the others rather than instead of them.
* **The odds are optional.** A line with no price is still a line — it is the book's midpoint
  — and it derives a value equal to itself (see `app.ranking.market`). Storing null rather
  than a made-up -110 keeps "no price" and "an even price" distinguishable, even though the
  two currently derive the same number.

The derived per-player number does NOT live here. It is written to `Projection` under this
source, so the market reaches the board through the same value-source adapter every other
projection uses — one pool, one age curve, one percentile scale.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player
from app.scoring.stats import stat_name


class MarketLine(Base):
    """A book's over/under on one stat, per game, with the price on each side."""

    __tablename__ = "market_line"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "season",
            "player_id",
            "stat_id",
            name="uq_market_line_source_season_player_stat",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Which book (or aggregator) published it: 'market' by default, so the derived projection
    # lands as `projection:market`. A second book is a second source, side by side.
    source: Mapped[str] = mapped_column(String(24), nullable=False)

    # The season this line is FOR, labelled as `Projection.season` and `AdpEntry.season` are
    # (the year the season ends: 2027 is 2026-27). Part of the key, so last season's lines
    # survive this season's.
    season: Mapped[int] = mapped_column(nullable=False)

    # ESPN's stat id (`app.scoring.stats`) rather than a name, because the id is what the
    # league's scoring coefficients are keyed by — storing the name would mean translating
    # twice and risking a second vocabulary.
    stat_id: Mapped[int] = mapped_column(nullable=False)

    # The line itself, PER GAME. Season-long props are quoted per game far more often than as
    # totals, and per game is what the board ranks on, so there is no basis flag here the way
    # there is on a projection import — one shape, stated once.
    line: Mapped[float] = mapped_column(Float, nullable=False)

    # American odds on each side (-135, +110). Null means the price wasn't published or wasn't
    # entered, which derives the line untouched — see `app.ranking.market.devig`.
    over_odds: Mapped[int | None] = mapped_column()
    under_odds: Mapped[int | None] = mapped_column()

    # When these values last changed, not when we last looked — so it reads as "the book moved
    # this line on...".
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="market_lines")

    @property
    def stat_name(self) -> str:
        """'PTS', 'AST', ... — the name the scoring engine and the derived stat line use."""
        return stat_name(self.stat_id)

    def __repr__(self) -> str:
        return (
            f"MarketLine(player_id={self.player_id!r}, source={self.source!r}, "
            f"season={self.season!r}, stat={self.stat_name}, line={self.line!r}, "
            f"over={self.over_odds!r}, under={self.under_odds!r})"
        )
