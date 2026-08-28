"""A named, ordered list of players from one source — a board, not a per-player number.

`AdpEntry` and `Projection` both store *a number about a player*, so one row per (player,
source, season) says everything. A ranking doesn't work that way: it is a **set** with an
order, and the interesting facts about it are collective. Who is on it, who fell off it since
last week, and where the tier breaks are — none of which survive being flattened into a
per-player column.

Hence two tables. `RankingSet` is the list itself (one source's "Dynasty Top 200" for one
season); `RankingEntry` is a player's place on it. The consequences that matter downstream:

* **A player can be on many sets.** The consensus board, an expert's top 200 and our own list
  coexist for the same season and are compared side by side (FEATURE_SPEC 5).
* **A set is replaced, not merged.** Version two of a list is a different list: players drop
  off it. `app.ingest.ranking` therefore rewrites a set's entries wholesale, which is why the
  cascade below is `delete-orphan` and the FK is `ON DELETE CASCADE` — deleting the set has to
  take its entries with it, from either end.
* **Rank is stored, not derived.** A source that prints 1..200 with gaps, or that ranks by
  tier, means what it printed; re-deriving order from `value` would quietly disagree with it.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player


class RankingSet(Base):
    """One named ordered list, from one source, for one season."""

    __tablename__ = "ranking_set"
    __table_args__ = (
        UniqueConstraint("source", "name", "season", name="uq_ranking_set_source_name_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # Who published it: 'hashtag', 'fantasypros', 'us'. Same vocabulary as `AdpEntry.source`.
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    # The human label — "Dynasty Top 200", "Rest of Season", "Our Board". A source publishes
    # more than one list, so the name is part of the identity rather than a caption: it is
    # what makes re-importing *this* list replace *this* list and leave the others alone.
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # The season the list is FOR, labelled as `AdpEntry.season` is (the year the season ends).
    season: Mapped[int] = mapped_column(nullable=False)

    # When the set was last (re)imported. Unlike `as_of` on AdpEntry/Projection this does move
    # on every import, changed contents or not: a ranking's freshness is a property of the
    # list, and "this board is three weeks old" is the question people ask of it.
    as_of: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entries: Mapped[list["RankingEntry"]] = relationship(
        back_populates="ranking_set",
        cascade="all, delete-orphan",
        order_by="RankingEntry.rank",
    )

    def __repr__(self) -> str:
        return (
            f"RankingSet(id={self.id!r}, source={self.source!r}, name={self.name!r}, "
            f"season={self.season!r})"
        )


class RankingEntry(Base):
    """One player's place on one ranking set."""

    __tablename__ = "ranking_entry"
    __table_args__ = (
        UniqueConstraint("ranking_set_id", "player_id", name="uq_ranking_entry_set_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ranking_set_id: Mapped[int] = mapped_column(
        ForeignKey("ranking_set.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Where the source put them. Read off a rank column when the file has one, otherwise the
    # row's position in the file — see `app.ingest.ranking`, which is also why ranks can have
    # gaps: a row held for review leaves its number empty rather than shifting everyone up.
    rank: Mapped[int] = mapped_column(nullable=False)

    # Whatever the source calls its tier — "1", "Tier 2", "Elite". Text, not a number, because
    # a tier is a label: half of sources number them and the other half name them, and storing
    # "Elite" as a null would throw away the only thing the column said.
    tier: Mapped[str | None] = mapped_column(String(40))

    # The score the source printed beside the rank, if any (a composite, a projected value, a
    # dollar figure). Kept as published and never re-scored: it is the source's opinion, and
    # our own pricing lives in `Projection`.
    value: Mapped[float | None] = mapped_column(Float)

    ranking_set: Mapped[RankingSet] = relationship(back_populates="entries")
    player: Mapped[Player] = relationship(back_populates="ranking_entries")

    def __repr__(self) -> str:
        return (
            f"RankingEntry(set={self.ranking_set_id!r}, player_id={self.player_id!r}, "
            f"rank={self.rank!r}, tier={self.tier!r})"
        )
