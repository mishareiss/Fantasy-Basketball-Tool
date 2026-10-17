"""projection imputed stats

Puts `imputed_stats` on `projection`: which stats we filled in because the source doesn't
project them (ESPN publishes no DD, TD, OREB, DREB or PF), and how. Nullable, no default:
every existing row imputed nothing, and null says exactly that.

`batch_alter_table` so the same migration runs on SQLite (the tests); on Postgres it is a
plain ALTER.

Revision ID: a3c7e9d2b416
Revises: b7e2c5d81f04
Create Date: 2026-10-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3c7e9d2b416"
down_revision: str | Sequence[str] | None = "b7e2c5d81f04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("projection") as batch:
        batch.add_column(sa.Column("imputed_stats", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema. Loses the record of what was imputed; the imputed values stay in the
    stat maps until the next sync rewrites them."""
    with op.batch_alter_table("projection") as batch:
        batch.drop_column("imputed_stats")
