"""plan move timing

Puts `timing` on `plan_move`: whether a move is made after its day's games tip off (the
dropped player's game counts and the pickup plays from the next day) or before them.

Every row that exists before this migration was written meaning "both halves on that day",
which is `before_games`, so that is the server default and what existing rows get. New moves
default to `after_tipoff` in the API, not here: a row inserted without a timing is still an
old-style move.

`batch_alter_table` because the column comes with a CHECK constraint, which SQLite (the tests)
can only add by rebuilding the table. On Postgres it is a plain ALTER.

Revision ID: b7e2c5d81f04
Revises: e4b9a2c7d150
Create Date: 2026-10-16 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e2c5d81f04"
down_revision: str | Sequence[str] | None = "e4b9a2c7d150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("plan_move") as batch:
        batch.add_column(
            sa.Column(
                "timing",
                sa.String(length=16),
                server_default="before_games",
                nullable=False,
            )
        )
        batch.create_check_constraint(
            "ck_plan_move_timing", "timing IN ('after_tipoff', 'before_games')"
        )


def downgrade() -> None:
    """Downgrade schema. Loses each move's timing: they all read as before-games again."""
    with op.batch_alter_table("plan_move") as batch:
        batch.drop_constraint("ck_plan_move_timing", type_="check")
        batch.drop_column("timing")
