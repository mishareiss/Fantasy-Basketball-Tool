"""espn foundation: players, aliases, league settings, scoring rules

Creates the real domain schema and drops the `health_check` placeholder table that existed
only so the initial migration had something to generate. GET /health and GET /health/db keep
working — /health/db just runs SELECT 1 and never needed a table.

Revision ID: c98854d86b74
Revises: b9ba52b7094b
Create Date: 2026-08-22 23:09:26.902742

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c98854d86b74"
down_revision: str | Sequence[str] | None = "b9ba52b7094b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "league_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("espn_league_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("scoring_type", sa.String(length=32), nullable=True),
        sa.Column("team_count", sa.Integer(), nullable=True),
        sa.Column("roster_slots", sa.JSON(), nullable=True),
        sa.Column(
            "synced_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("espn_league_id", "season", name="uq_league_settings_league_season"),
    )
    op.create_table(
        "player",
        sa.Column("espn_player_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("first_name", sa.String(length=60), nullable=True),
        sa.Column("last_name", sa.String(length=60), nullable=True),
        sa.Column("nba_team", sa.String(length=4), nullable=True),
        sa.Column("pro_team_id", sa.Integer(), nullable=True),
        sa.Column("primary_position", sa.String(length=8), nullable=True),
        sa.Column("positions", sa.JSON(), nullable=False),
        sa.Column("roster_status", sa.String(length=16), nullable=True),
        sa.Column("espn_fantasy_team_id", sa.Integer(), nullable=True),
        sa.Column("injury_status", sa.String(length=24), nullable=True),
        sa.Column("injured", sa.Boolean(), nullable=False),
        sa.Column("birthdate", sa.Date(), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("espn_player_id"),
    )
    op.create_index(op.f("ix_player_full_name"), "player", ["full_name"], unique=False)
    op.create_table(
        "player_alias",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "source_name", name="uq_player_alias_source_name"),
    )
    op.create_index(op.f("ix_player_alias_player_id"), "player_alias", ["player_id"], unique=False)
    op.create_table(
        "scoring_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("league_settings_id", sa.Integer(), nullable=False),
        sa.Column("stat_id", sa.Integer(), nullable=False),
        sa.Column("stat_name", sa.String(length=16), nullable=False),
        sa.Column("points", sa.Float(), nullable=False),
        sa.Column("is_reverse", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["league_settings_id"], ["league_settings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("league_settings_id", "stat_id", name="uq_scoring_rule_settings_stat"),
    )
    op.create_index(
        op.f("ix_scoring_rule_league_settings_id"),
        "scoring_rule",
        ["league_settings_id"],
        unique=False,
    )
    op.drop_table("health_check")


def downgrade() -> None:
    """Downgrade schema (restores the placeholder health_check table)."""
    op.create_table(
        "health_check",
        sa.Column("id", sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column(
            "checked_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("health_check_pkey")),
    )
    op.drop_index(op.f("ix_scoring_rule_league_settings_id"), table_name="scoring_rule")
    op.drop_table("scoring_rule")
    op.drop_index(op.f("ix_player_alias_player_id"), table_name="player_alias")
    op.drop_table("player_alias")
    op.drop_index(op.f("ix_player_full_name"), table_name="player")
    op.drop_table("player")
    op.drop_table("league_settings")
