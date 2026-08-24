"""espn projections and adp

Adds the two tables that turn the player pool into a board: `projection` (a full-season stat
line priced under our custom scoring, one row per player/source/kind/season) and `adp_entry`
(one source's draft-market read on a player). Both are additive — nothing existing changes.

Revision ID: 85e6259342d9
Revises: c98854d86b74
Create Date: 2026-08-23 14:58:25.396076

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85e6259342d9"
down_revision: str | Sequence[str] | None = "c98854d86b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "adp_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("adp", sa.Float(), nullable=True),
        sa.Column("auction_value", sa.Float(), nullable=True),
        sa.Column("percent_owned", sa.Float(), nullable=True),
        sa.Column(
            "as_of", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", "source", name="uq_adp_entry_player_source"),
    )
    op.create_index(op.f("ix_adp_entry_player_id"), "adp_entry", ["player_id"], unique=False)
    op.create_table(
        "projection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("raw_stats", sa.JSON(), nullable=False),
        sa.Column("per_game_stats", sa.JSON(), nullable=True),
        sa.Column("projected_games", sa.Float(), nullable=True),
        sa.Column("fantasy_points_total", sa.Float(), nullable=False),
        sa.Column("fantasy_points_per_game", sa.Float(), nullable=False),
        sa.Column("per_game_basis", sa.String(length=24), nullable=False),
        sa.Column("source_fantasy_points_total", sa.Float(), nullable=True),
        sa.Column(
            "as_of", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "player_id", "source", "kind", "season", name="uq_projection_player_source_kind_season"
        ),
    )
    op.create_index(op.f("ix_projection_player_id"), "projection", ["player_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema (drops both tables; they hold only re-syncable data)."""
    op.drop_index(op.f("ix_projection_player_id"), table_name="projection")
    op.drop_table("projection")
    op.drop_index(op.f("ix_adp_entry_player_id"), table_name="adp_entry")
    op.drop_table("adp_entry")
