"""Where our board breaks into tiers: cut ranks, per scope. Not a tier per player.

The table is four columns wide and the interesting one is the column that ISN'T here:
`player_id`. A row says "a new tier starts at rank 12 of the point guards" and names nobody,
which is the entire design (`app.ranking.tiers` argues it at length).

Why not store a tier on `master_rank_entry` instead — one more column, no new table? Because
the board is dragged. A tier-per-player column has to be rewritten on every reorder, and the
rewrite has no correct answer: when a player is dragged from 14 to 3, does he bring tier 4
with him into the middle of tier 1, or take tier 1 from the slot he landed in? Both are
defensible and both are wrong half the time. Bands have no such question — rank 3 is in the
tier-1 band, so he is tier 1, and nothing was written at all.

Why cut ranks rather than (tier, start, end): a band's end is the next band's start, so storing
both is storing one fact twice and inviting them to disagree. The list of starts is the whole
structure, and it always begins with 1 because tier 1 starts at the top of the board — which
doubles as the "this scope has been seeded" marker, so a scope that auto-tiered into one
undivided tier doesn't re-seed itself on every read.

Why `scope` is a plain string rather than an enum: same argument as `master_rank_entry.tag`.
The vocabulary is a constant tuple validated at the API edge, so adding a 'G'/'F' combined
scope later is a line here and a UI chip, not a migration on Postgres and a table rebuild on
SQLite.

Cut ranks are scope-relative. In 'overall' a cut rank is a board rank; in 'PG' it is a rank
among the point guards on the board, so the eighth-best point guard sits at PG-rank 8 whatever
his overall rank is. A foreign key would be meaningless here for the same reason the player id
is absent — there is nothing to point at but a position in a list.
"""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The whole board, tiered as one list.
SCOPE_OVERALL = "overall"

# Every scope a set of cuts can be stored for: the board, plus one sub-order per position.
# The order matters only in that it is the order responses list scopes in.
TIER_SCOPES = (SCOPE_OVERALL, "PG", "SG", "SF", "PF", "C")


class MasterTierBreak(Base):
    """One tier boundary: in this scope, a new tier starts at this rank."""

    __tablename__ = "master_tier_break"
    __table_args__ = (
        # One divider per slot. Two rows saying "a tier starts at rank 12" would be one tier
        # boundary counted twice, which `tiers_for` would read as two tiers of zero players
        # if it trusted the rows; the constraint means it never has to.
        UniqueConstraint("scope", "cut_rank", name="uq_master_tier_break_scope_cut"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    # 'overall' | 'PG' | 'SG' | 'SF' | 'PF' | 'C'. Validated against TIER_SCOPES at the API
    # edge (422). Indexed because every read of this table is "the cuts for one scope".
    scope: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    # The 1-based rank, WITHIN THIS SCOPE'S ORDER, that a tier starts at. 1 is always present
    # on a seeded scope — tier 1 starts at the top.
    cut_rank: Mapped[int] = mapped_column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"MasterTierBreak(scope={self.scope!r}, cut_rank={self.cut_rank!r})"
