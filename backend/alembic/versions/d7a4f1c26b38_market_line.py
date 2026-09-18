"""market line

Adds `market_line`: one sportsbook over/under per (source, season, player, stat).

Why a table of its own rather than columns on `projection`: a line is per STAT and moves per
stat. The assists line reprices without the points line moving, and the key here is what lets
that one row be updated in place while the rest stand. The per-player number the board
actually ranks on is DERIVED from these rows into `projection` (source 'market'), so nothing
downstream needs a second value path — see `app.ingest.market_line`.

Why the odds are nullable: a line with no published price is still a line, and it derives a
value exactly equal to itself. Storing null rather than a made-up -110 keeps "no price" and
"an even price" tellable apart later, even though today they derive the same number.

Pure `create_table` — nothing existing is altered — so it applies unchanged on SQLite (which
is what lets the migration test drive it offline) and on Postgres, where `make migrate` is
still the acceptance check.

Revision ID: d7a4f1c26b38
Revises: c41d9a7e5b30
Create Date: 2026-09-17 22:45:11.004213

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a4f1c26b38"
down_revision: str | Sequence[str] | None = "c41d9a7e5b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE = "uq_market_line_source_season_player_stat"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "market_line",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("stat_id", sa.Integer(), nullable=False),
        sa.Column("line", sa.Float(), nullable=False),
        sa.Column("over_odds", sa.Integer(), nullable=True),
        sa.Column("under_odds", sa.Integer(), nullable=True),
        sa.Column(
            "as_of", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "season", "player_id", "stat_id", name=UNIQUE),
    )
    op.create_index(op.f("ix_market_line_player_id"), "market_line", ["player_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and every line in it. Lossy, and unlike ESPN's projections these do not
    re-sync — they were typed in by hand — so export anything worth keeping first. The derived
    `projection` rows under source 'market' are NOT removed: they are ordinary projections as
    far as the schema is concerned, and deleting rows of another table on the way down would
    be a surprise. Delete them by hand if a downgrade is meant to un-say the market's opinion.
    """
    op.drop_index(op.f("ix_market_line_player_id"), table_name="market_line")
    op.drop_table("market_line")
