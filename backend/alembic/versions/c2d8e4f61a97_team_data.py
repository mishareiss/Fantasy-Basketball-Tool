"""team data: NBA schedule, fantasy teams, rosters, lineups, transactions

The in-season Team tab's tables (`app.db.models.team`), plus two columns:

* `player.eligible_slots`: ESPN's raw eligible-slot ids, so a lineup move can be checked
  against the slot itself rather than the readable `positions` list.
* `league_settings.acquisition_settings`: ESPN's `acquisitionSettings` object, raw (adds per
  matchup, waiver hours).

Both columns are nullable with no backfill: NULL is "not synced since this migration", and the
next `make sync` fills them. The two ALTERs go through `batch_alter_table` so the migration
applies on the SQLite the tests run on as well as on Postgres.

Revision ID: c2d8e4f61a97
Revises: a71f4e0c9d53
Create Date: 2026-10-15 17:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d8e4f61a97"
down_revision: str | Sequence[str] | None = "a71f4e0c9d53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "nba_game",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("scoring_period_id", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("home_pro_team_id", sa.Integer(), nullable=False),
        sa.Column("away_pro_team_id", sa.Integer(), nullable=False),
        sa.Column("start_time_tbd", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nba_game_season_period", "nba_game", ["season", "scoring_period_id"], unique=False
    )

    op.create_table(
        "fantasy_team",
        sa.Column("season", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("team_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("abbrev", sa.String(length=16), nullable=True),
        sa.Column("owners", sa.JSON(), nullable=False),
        sa.Column("is_mine", sa.Boolean(), nullable=False),
        sa.Column("acquisitions_total", sa.Integer(), nullable=False),
        sa.Column("matchup_acquisition_totals", sa.JSON(), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("season", "team_id"),
    )

    op.create_table(
        "roster_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("lineup_slot_id", sa.Integer(), nullable=False),
        sa.Column("acquisition_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acquisition_type", sa.String(length=24), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "player_id", name="uq_roster_entry_season_player"),
    )
    op.create_index(
        "ix_roster_entry_season_team", "roster_entry", ["season", "team_id"], unique=False
    )

    op.create_table(
        "lineup_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("scoring_period_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("lineup_slot_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "season",
            "team_id",
            "scoring_period_id",
            "player_id",
            name="uq_lineup_assignment_day_player",
        ),
    )

    op.create_table(
        "espn_transaction",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=True),
        sa.Column("scoring_period_id", sa.Integer(), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_espn_transaction_season_type", "espn_transaction", ["season", "type"], unique=False
    )

    with op.batch_alter_table("player") as batch_op:
        batch_op.add_column(sa.Column("eligible_slots", sa.JSON(), nullable=True))
    with op.batch_alter_table("league_settings") as batch_op:
        batch_op.add_column(sa.Column("acquisition_settings", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    Drops every in-season table and both columns. All of it is re-pullable from ESPN with
    `make sync` (and `make sync-lineup`), EXCEPT manual lineup edits, which are lost.
    """
    with op.batch_alter_table("league_settings") as batch_op:
        batch_op.drop_column("acquisition_settings")
    with op.batch_alter_table("player") as batch_op:
        batch_op.drop_column("eligible_slots")

    op.drop_index("ix_espn_transaction_season_type", table_name="espn_transaction")
    op.drop_table("espn_transaction")
    op.drop_table("lineup_assignment")
    op.drop_index("ix_roster_entry_season_team", table_name="roster_entry")
    op.drop_table("roster_entry")
    op.drop_table("fantasy_team")
    op.drop_index("ix_nba_game_season_period", table_name="nba_game")
    op.drop_table("nba_game")
