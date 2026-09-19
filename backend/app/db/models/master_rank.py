"""OUR board: one explicitly-ordered list of players, arranged by hand and kept.

Every other ranking table in here stores somebody else's opinion. `RankingSet` holds a list we
imported, `AdpEntry` holds what a room did, `Projection` holds what a model expects — and the
consensus board (`app.ranking.consensus`) averages them into a view that is *recomputed from
scratch on every request*. That is the right shape for a view over other people's work: it has
no memory, and it shouldn't, because the answer changes when the inputs do.

This table is the opposite kind of object, and the difference is the whole point of it:

* **The order is STORED, not derived.** A rank here is a decision somebody made, so it is the
  data. The consensus moving a player twenty places does not move him on this board — it moves
  the REFERENCE column next to him, which is exactly the thing worth looking at ("I have him
  eight spots above the field"). A board that re-sorted itself under you would be a consensus
  board with extra steps.
* **One row per player, one board.** No source, no season, no horizon in the key: `player_id`
  is unique. The horizon on `GET /master/board` picks which consensus the reference column is
  computed from; it does not select a different set of ranks, because a person has one draft
  board and re-ordering two of them by hand is not a thing anyone does.
* **Excluded is not deleted.** Setting a player aside takes him out of the ORDER (`rank` goes
  null) and leaves the row, so the tag and the note he carries survive being parked — and so
  restoring him is one write rather than a re-litigation. `rank IS NULL` iff `excluded`, which
  is the invariant every write in `app.ranking.master` maintains.

What is deliberately NOT here: a per-horizon order (see above), a history of the board, and
any notion of "new". A player who has no row yet is new *by not being here*, which is what
`app.ranking.master` reconciles on read — it inserts him at the slot the consensus implies and
flags him on that response, rather than storing a freshness bit that nothing would ever clear.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.models.player import Player

# What a player can be flagged as, beyond his place. Two values because they are the two
# decisions a board actually records about a player you have already ranked: he is someone to
# reach for, or someone to let the room have. Extensible — this tuple is the whole validation,
# and the column is a string rather than a native enum so adding 'watch' later is a constant
# here plus a UI chip, not a migration on Postgres and a rebuild on SQLite.
TAG_TARGET = "target"
TAG_FADE = "fade"
MASTER_TAGS = (TAG_TARGET, TAG_FADE)


class MasterRankEntry(Base):
    """One player's place on our own board, plus whatever we've said about him."""

    __tablename__ = "master_rank_entry"
    # `updated_at` is a server default, and a seed inserts a thousand rows at once: without
    # this, rendering each one expires and re-reads it a row at a time. RETURNING fetches it
    # in the INSERT instead, on both Postgres and the SQLite the tests run on.
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # One board, so one entry per player. Stated as a named constraint rather than as
        # `unique=True` on the column so the reconcile in `app.ranking.master` can lean on the
        # name when it explains a conflict, and so the migration and the model agree on it.
        UniqueConstraint("player_id", name="uq_master_rank_entry_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # His 1-based place on the board. NULL iff `excluded` — a player who has been set aside
    # has no place, and a zero or a sentinel 9999 would sort him somewhere rather than nowhere.
    # Contiguous 1..N across the non-excluded entries: every write reflows the rest, so "rank"
    # always means what it says and the UI never has to renumber.
    rank: Mapped[int | None] = mapped_column()

    # Set aside: off the order, still on the board. See the module docstring.
    excluded: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())

    # 'target' | 'fade', or null for neither. Validated against MASTER_TAGS at the API edge
    # (422), not by a CHECK constraint: the vocabulary is meant to grow, and a constraint would
    # make growing it a migration.
    tag: Mapped[str | None] = mapped_column(String(16))

    # Free text, as long as it needs to be — "only at a discount", "check the knee in March".
    # Text rather than String(n) because a note nobody can finish is a note nobody writes.
    note: Mapped[str | None] = mapped_column(Text)

    # When this row last changed: a rank moved, a tag set, a note edited. Unlike the `as_of`
    # columns on the imported tables this is about OUR edit, not about a source's freshness.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    player: Mapped[Player] = relationship(back_populates="master_rank")

    def __repr__(self) -> str:
        return (
            f"MasterRankEntry(player_id={self.player_id!r}, rank={self.rank!r}, "
            f"excluded={self.excluded!r}, tag={self.tag!r})"
        )
