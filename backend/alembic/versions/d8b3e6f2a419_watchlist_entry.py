"""watchlist entry

The waiver watchlist (`app.db.models.watchlist`): one row per watched player per season,
shared by both co-managers.

A new table only, so plain `create_table` with its constraints inline: nothing here needs
`batch_alter_table`, and it applies unchanged on the SQLite the tests run on and on Postgres.

Revision ID: d8b3e6f2a419
Revises: c2d8e4f61a97
Create Date: 2026-10-15 22:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b3e6f2a419"
down_revision: str | Sequence[str] | None = "c2d8e4f61a97"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "watchlist_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season", "player_id", name="uq_watchlist_entry_season_player"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("watchlist_entry")
