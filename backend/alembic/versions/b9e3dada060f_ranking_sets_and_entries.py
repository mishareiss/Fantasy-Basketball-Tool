"""ranking sets and entries

Adds the two tables behind the `ranking` import kind: `ranking_set` (one named, ordered list
from one source for one season) and `ranking_entry` (a player's place on it). Purely additive
— no existing table changes.

Two tables rather than a column, because a ranking is a *set* with an order and not a number
about a player: its composition is the signal (who is on it, who fell off), and a per-player
column can't hold that. `RankingEntry.ranking_set_id` cascades so replacing or deleting a set
takes its entries with it, which is what the import relies on — a re-import rewrites a set's
entries wholesale rather than upserting them row by row.

Both tables are generic SQL (no PG-only types), so the migration test can drive it against a
throwaway SQLite file offline; Postgres remains the acceptance check via `make migrate`.

Revision ID: b9e3dada060f
Revises: 637150ee8d91
Create Date: 2026-08-28 05:41:45.6N

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b9e3dada060f"
down_revision: str | Sequence[str] | None = "637150ee8d91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "ranking_set",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column(
            "as_of", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "name", "season", name="uq_ranking_set_source_name_season"),
    )
    op.create_table(
        "ranking_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ranking_set_id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("tier", sa.String(length=40), nullable=True),
        sa.Column("value", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ranking_set_id"], ["ranking_set.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ranking_set_id", "player_id", name="uq_ranking_entry_set_player"),
    )
    op.create_index(
        op.f("ix_ranking_entry_player_id"), "ranking_entry", ["player_id"], unique=False
    )
    op.create_index(
        op.f("ix_ranking_entry_ranking_set_id"), "ranking_entry", ["ranking_set_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops both tables. Lossy for imported rankings — they are a pasted file, not something that
    re-syncs from an API — so export anything that matters before going back past this.
    """
    op.drop_index(op.f("ix_ranking_entry_ranking_set_id"), table_name="ranking_entry")
    op.drop_index(op.f("ix_ranking_entry_player_id"), table_name="ranking_entry")
    op.drop_table("ranking_entry")
    op.drop_table("ranking_set")
