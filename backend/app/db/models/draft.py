"""A live draft: one row for the room's shape, and one row per pick made in it.

`app.draft` can run a whole draft without a database — that is the point of it. These two
tables are the other half: the draft Misha is actually sitting in, so closing the laptop at
pick 34 and coming back does not lose it, and so the availability numbers on the plan are
computed against the picks that really happened rather than against a board in a browser tab.

ONE ACTIVE DRAFT. `draft` holds a single row, not a session per mock. There is one startup a
year and one seat in it; a list of saved drafts would be a feature with an owner, a name and a
picker, and none of those exist. `POST /draft` refuses a second one (409) unless it is told to
replace it.

THE CONFIG IS A SNAPSHOT, which is the one thing here that looks like duplication and isn't.
`team_count`, `rounds`, `my_slot` and `roster_slots` all have a source — `DRAFT_*` in
`Settings`, ESPN's `lineupSlotCounts` — and they are copied in anyway, because a draft is a
log of pick NUMBERS and a pick number only means something under one shape. If `DRAFT_MY_SLOT`
changed between pick 20 and pick 40, a draft that read its seat from the environment would
silently re-label every pick already made. Snapshotting makes the row self-describing: it
replays to exactly the state it was in (`app.draft.session.build_state`) whatever the
environment has since become. `field_horizon` and `field_source_ids` are snapshotted for the
same reason one step removed — they decide WHICH consensus the simulated room drafts off, so
they are part of how this draft was modelled, not a per-request preference.

THE LOG IS MODE-AGNOSTIC AND LINEAR. A `draft_pick` row is a pick: a number, a seat, a player.
Manual entry and a simulated opponent pick produce the same row, distinguished only by
`is_auto` — so undo is one delete of the highest `pick_number` whatever made it, and replay is
one ordered scan. `mode` on the draft is a UI preference (does the room advance itself?), NOT a
constraint: both entering a pick by hand and advancing the sim work in either mode.

WHAT IS DELIBERATELY NOT STORED: availability. Those percentages are a Monte Carlo over the
state, recomputed on request and cheap (`app.draft.availability`); persisting them would be
persisting a derivation that goes stale the instant the next pick lands. Also not stored: a
pick that took nobody. `DraftState.pass_pick` exists so a *projection* can step over my seat
without drafting for me, and a projection is not a draft — every row in this table has a
player in it.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Does the room advance itself, or is every seat typed in? A UI preference stored with the
# draft so the page comes back the way it was left — the API enforces neither, because
# correcting a mis-entered pick in a simulated draft and auto-picking a stalled room in a
# manual one are both things that happen. Validated at the API edge (422) against this tuple,
# the same way `MASTER_TAGS` and `TIER_SCOPES` are: a string column and a constant, so a third
# mode later is a line here and a UI chip rather than a migration on Postgres and a table
# rebuild on SQLite.
MODE_SIMULATION = "simulation"
MODE_MANUAL = "manual"
DRAFT_MODES = (MODE_SIMULATION, MODE_MANUAL)


class Draft(Base):
    """The current draft: the room's shape, the field it is modelled against, and the mode."""

    __tablename__ = "draft"
    # `created_at` / `updated_at` are server defaults and every write answers with the state
    # it produced, so without this the INSERT is followed by a SELECT to render the response.
    # RETURNING fetches them in the INSERT instead, on Postgres and on the SQLite tests run on.
    __mapper_args__ = {"eager_defaults": True}

    id: Mapped[int] = mapped_column(primary_key=True)

    # --- the config snapshot (see the module docstring) -------------------------------------
    team_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rounds: Mapped[int] = mapped_column(Integer, nullable=False)
    # 1-based seat, counted the way round 1 runs. At 2 of 10 my picks are 2, 19, 22, 39, ...
    my_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    # ESPN's `lineupSlotCounts` shape, `{'PG': 1, ..., 'UT': 2, 'BE': 13}`. JSON rather than a
    # table because nothing ever queries it — it is handed to `DraftConfig` whole, and only
    # the five dedicated starter slots in it affect anything (`app.draft.needs`).
    roster_slots: Mapped[dict] = mapped_column(JSON, nullable=False)

    # --- which board the simulated room drafts off -----------------------------------------
    # A value horizon ('dynasty' | 'current_year'), validated at the API edge by the same
    # `load_catalog` every board endpoint uses.
    field_horizon: Mapped[str] = mapped_column(String(32), nullable=False)
    # The source ids the field is assumed to read, or NULL for every source the horizon
    # offers — which is not the same thing as an empty list, so this stays nullable rather
    # than defaulting to `[]`. A room that visibly drafts ADP is modelled with `['adp:espn']`.
    field_source_ids: Mapped[list | None] = mapped_column(JSON)

    # 'simulation' | 'manual'. A preference, not a constraint (see the module docstring).
    mode: Mapped[str] = mapped_column(String(16), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Bumped by every pick, undo and reset — "when did this draft last move".
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    picks: Mapped[list["DraftPick"]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="DraftPick.pick_number",
    )

    def __repr__(self) -> str:
        return (
            f"Draft(id={self.id!r}, team_count={self.team_count!r}, rounds={self.rounds!r}, "
            f"my_slot={self.my_slot!r}, mode={self.mode!r})"
        )


class DraftPick(Base):
    """One pick that happened: its number, the seat that made it, and who it took."""

    __tablename__ = "draft_pick"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        # The log is the draft, so a pick number can exist once in it. Two rows at pick 19
        # would make "who was available at 19" a question with two answers, and replay would
        # pick whichever the database returned first. Named so the model, the migration and
        # the message all agree on it. On both dialects this doubles as the index every read
        # of the log uses — the whole log of one draft, in pick order.
        UniqueConstraint("draft_id", "pick_number", name="uq_draft_pick_draft_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("draft.id", ondelete="CASCADE"), nullable=False
    )

    # 1-based, in the flattened snake order of `DraftConfig.pick_order`. Contiguous 1..N: the
    # log has no holes, because a draft cannot skip a pick and come back to it.
    pick_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Whose pick it was. Derivable from `pick_number` under the draft's shape, and stored
    # anyway — it is what the row asserts, and `apply_pick` checks the two against each other
    # rather than trusting either alone.
    team_slot: Mapped[int] = mapped_column(Integer, nullable=False)
    # Who it took. NOT NULL: a pick that took nobody is a projection stepping over my seat,
    # and that never reaches this table (see the module docstring).
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False, index=True
    )

    # The simulated field made this pick, rather than it being entered by hand. The only thing
    # that distinguishes the two kinds of row, and it is for display and for honesty about
    # where the state came from — undo and replay treat them identically.
    is_auto: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    draft: Mapped[Draft] = relationship(back_populates="picks")

    def __repr__(self) -> str:
        return (
            f"DraftPick(pick_number={self.pick_number!r}, team_slot={self.team_slot!r}, "
            f"player_id={self.player_id!r}, is_auto={self.is_auto!r})"
        )
