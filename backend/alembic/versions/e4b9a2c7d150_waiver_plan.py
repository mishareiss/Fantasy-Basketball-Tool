"""waiver plan

The waiver plan (`app.db.models.plan`): planned moves, their ordered pickup candidates, the
Plan view's own lineup, and per-matchup overrides of ESPN's acquisition count.

New tables only, so plain `create_table` with constraints inline: nothing here alters an
existing table, and it applies unchanged on the SQLite the tests run on and on Postgres.

Revision ID: e4b9a2c7d150
Revises: d8b3e6f2a419
Create Date: 2026-10-15 23:45:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4b9a2c7d150"
down_revision: str | Sequence[str] | None = "d8b3e6f2a419"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        )
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "plan_move",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("scoring_period_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("drop_player_id", sa.Integer(), nullable=True),
        sa.Column("drop_from_move_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("resolved_player_id", sa.Integer(), nullable=True),
        sa.Column("espn_transaction_id", sa.String(length=36), nullable=True),
        sa.Column("drop_mismatch", sa.Boolean(), nullable=False),
        sa.Column("detected_drop_player_id", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "(drop_player_id IS NULL) <> (drop_from_move_id IS NULL)",
            name="ck_plan_move_one_drop",
        ),
        sa.CheckConstraint("status IN ('planned', 'done', 'skipped')", name="ck_plan_move_status"),
        sa.ForeignKeyConstraint(["drop_player_id"], ["player.espn_player_id"]),
        sa.ForeignKeyConstraint(["drop_from_move_id"], ["plan_move.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resolved_player_id"], ["player.espn_player_id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_plan_move_season_team_period",
        "plan_move",
        ["season", "team_id", "scoring_period_id"],
        unique=False,
    )
    op.create_table(
        "plan_candidate",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("move_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["move_id"], ["plan_move.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("move_id", "player_id", name="uq_plan_candidate_move_player"),
        sa.UniqueConstraint("move_id", "rank", name="uq_plan_candidate_move_rank"),
    )
    op.create_table(
        "plan_lineup_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("scoring_period_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("lineup_slot_id", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season",
            "team_id",
            "scoring_period_id",
            "player_id",
            name="uq_plan_lineup_assignment_day_player",
        ),
    )
    op.create_table(
        "acquisition_override",
        sa.Column("season", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("team_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("matchup_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("season", "team_id", "matchup_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("acquisition_override")
    op.drop_table("plan_lineup_assignment")
    op.drop_table("plan_candidate")
    op.drop_index("ix_plan_move_season_team_period", table_name="plan_move")
    op.drop_table("plan_move")
