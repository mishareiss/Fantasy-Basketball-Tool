"""master rank entry

Adds `master_rank_entry`: OUR board — one row per player, holding the rank we put him at plus
the tag, the note and whether he's been set aside.

Why a table rather than a column on `player`: the board is a set with an order, and the order
is the data. A rank here is a decision, not a derivation — which is the whole difference from
`GET /board/consensus`, recomputed from the sources on every request. Nothing in the sources
moving is allowed to move a rank in this table; see `app.ranking.master`.

Why `player_id` is UNIQUE with no source/season/horizon beside it: there is one board. The
horizon on `GET /master/board` chooses which consensus the reference column is computed
against, not which set of ranks to read, because a person has one draft board.

Why `rank` is nullable: NULL iff `excluded`. A player set aside has no place in the order, and
a 0 or a sentinel 9999 would sort him somewhere rather than nowhere. The invariant is
maintained by `app.ranking.master`, which also keeps the live ranks contiguous 1..N.

Why `tag` is a plain string rather than an enum or a CHECK: 'target' | 'fade' is validated at
the API edge against `app.db.models.master_rank.MASTER_TAGS`, so adding 'watch' later is a
constant and a UI chip instead of a migration on Postgres and a table rebuild on SQLite.

Pure `create_table` — nothing existing is altered — so it applies unchanged on SQLite (which
is what lets the migration test drive it offline) and on Postgres, where `make migrate` is
still the acceptance check.

Revision ID: a3f27c91b054
Revises: d7a4f1c26b38
Create Date: 2026-09-18 10:12:44.882017

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f27c91b054"
down_revision: str | Sequence[str] | None = "d7a4f1c26b38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE = "uq_master_rank_entry_player"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "master_rank_entry",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("excluded", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("tag", sa.String(length=16), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        # `func.now()` rather than the `text("now()")` the older migrations use, because this
        # is the first table whose rows are written by hand rather than by a sync: an INSERT
        # that omits the timestamp has to work, and a literal `now()` is not a function SQLite
        # has. Compiles to `now()` on Postgres and CURRENT_TIMESTAMP on SQLite, so the offline
        # migration test can insert a board row the way the application does.
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("player_id", name=UNIQUE),
    )
    op.create_index(
        op.f("ix_master_rank_entry_player_id"), "master_rank_entry", ["player_id"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and the whole board with it: every rank, tag and note. Lossy in the way
    that matters most in this repo — unlike a projection or an ADP table, none of this
    re-syncs, because none of it came from anywhere. It was typed. Export it before going back
    past this revision; `GET /master/board` will happily re-seed afterwards, but it will re-seed
    from the CONSENSUS, which is precisely the opinion the board existed to disagree with.
    """
    op.drop_index(op.f("ix_master_rank_entry_player_id"), table_name="master_rank_entry")
    op.drop_table("master_rank_entry")
