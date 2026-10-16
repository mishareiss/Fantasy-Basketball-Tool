"""The waiver plan: the moves we intend to make, shared by both co-managers.

In the database rather than the browser so both of us edit one plan. A move is "on day D,
drop X, pick up Y" with ordered backups for Y; the rules engine that reads these rows is the
pure `app.team.plan`, and nothing here enforces a league rule. Rules only ever warn.

`PlanLineupAssignment` is the Plan view's own lineup. It is a separate table from
`LineupAssignment` on purpose, so a slot moved in the plan can never leak into the Actual
lineup, and a lineup pulled from ESPN can never overwrite a planned one.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import UTCDateTime

PLAN_PLANNED = "planned"
PLAN_DONE = "done"
PLAN_SKIPPED = "skipped"
PLAN_STATUSES = (PLAN_PLANNED, PLAN_DONE, PLAN_SKIPPED)


class PlanMove(Base):
    """On day `scoring_period_id`, drop one player and pick up the first available candidate.

    The drop is EITHER a named player (`drop_player_id`) OR "whoever move N picked up"
    (`drop_from_move_id`), never both and never neither; the check constraint says so.
    `sequence` orders moves on the same day (ties fall back to id).
    """

    __tablename__ = "plan_move"
    __table_args__ = (
        CheckConstraint(
            "(drop_player_id IS NULL) <> (drop_from_move_id IS NULL)",
            name="ck_plan_move_one_drop",
        ),
        CheckConstraint(
            "status IN ('planned', 'done', 'skipped')",
            name="ck_plan_move_status",
        ),
        Index("ix_plan_move_season_team_period", "season", "team_id", "scoring_period_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    scoring_period_id: Mapped[int] = mapped_column(nullable=False)
    sequence: Mapped[int] = mapped_column(nullable=False, default=0)
    drop_player_id: Mapped[int | None] = mapped_column(ForeignKey("player.espn_player_id"))
    # RESTRICT: a move another move's drop points at can't vanish underneath it. The API
    # refuses such a delete (409) unless asked to repoint the dependents first.
    drop_from_move_id: Mapped[int | None] = mapped_column(
        ForeignKey("plan_move.id", ondelete="RESTRICT")
    )
    # One of PLAN_STATUSES.
    status: Mapped[str] = mapped_column(String(8), nullable=False, default=PLAN_PLANNED)
    # Which candidate was actually added (auto-detected from ESPN, or chosen by hand).
    resolved_player_id: Mapped[int | None] = mapped_column(ForeignKey("player.espn_player_id"))
    espn_transaction_id: Mapped[str | None] = mapped_column(String(36))
    # Set by auto-detect when the ESPN move that completed this one dropped somebody other
    # than the planned drop (`detected_drop_player_id`, None = dropped nobody). Shown as an
    # info until the move is next edited, which counts as acknowledging it.
    drop_mismatch: Mapped[bool] = mapped_column(nullable=False, default=False)
    detected_drop_player_id: Mapped[int | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    candidates: Mapped[list["PlanCandidate"]] = relationship(
        back_populates="move",
        order_by="PlanCandidate.rank",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"PlanMove(id={self.id!r}, sp={self.scoring_period_id!r}, status={self.status!r})"


class PlanCandidate(Base):
    """One pickup candidate of a move: rank 1 is the primary, 2.. the backups in order."""

    __tablename__ = "plan_candidate"
    __table_args__ = (
        UniqueConstraint("move_id", "rank", name="uq_plan_candidate_move_rank"),
        UniqueConstraint("move_id", "player_id", name="uq_plan_candidate_move_player"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    move_id: Mapped[int] = mapped_column(
        ForeignKey("plan_move.id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False
    )

    move: Mapped[PlanMove] = relationship(back_populates="candidates")

    def __repr__(self) -> str:
        return f"PlanCandidate(move_id={self.move_id!r}, rank={self.rank!r})"


class PlanLineupAssignment(Base):
    """A slot set in the Plan view, for one player on one day. Never read by the Actual view."""

    __tablename__ = "plan_lineup_assignment"
    __table_args__ = (
        UniqueConstraint(
            "season",
            "team_id",
            "scoring_period_id",
            "player_id",
            name="uq_plan_lineup_assignment_day_player",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(nullable=False)
    team_id: Mapped[int] = mapped_column(nullable=False)
    scoring_period_id: Mapped[int] = mapped_column(nullable=False)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("player.espn_player_id", ondelete="CASCADE"), nullable=False
    )
    lineup_slot_id: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"PlanLineupAssignment(sp={self.scoring_period_id!r}, "
            f"player_id={self.player_id!r}, slot={self.lineup_slot_id!r})"
        )


class AcquisitionOverride(Base):
    """A hand-set "adds used" count for one matchup, replacing ESPN's for the plan's math.

    For unforeseen circumstances: ESPN's counter is the default and is right almost always.
    """

    __tablename__ = "acquisition_override"

    season: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    matchup_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    used: Mapped[int] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"AcquisitionOverride(matchup_id={self.matchup_id!r}, used={self.used!r})"
