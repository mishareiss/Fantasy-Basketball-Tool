"""draft team names

Puts `team_names` on `draft`: a JSON map from seat number (as a string key) to what that
seat is called.

Why a column on `draft` rather than a `draft_team` table: a seat is not an entity here. It
is a number the snake already owns, and the only thing this adds to it is a label. A table
would buy a foreign key nothing points at and a join every read of the draft has to do, for
a string that no pick, need, autopick weight or availability number ever looks at.

Why JSON with STRING keys: JSON objects have no integer keys, on Postgres or on the SQLite
the tests run on, so `{'1': 'Sam'}` is what round-trips identically on both. The API reads it
back by `str(slot)` for exactly that reason.

Why nullable rather than a `{}` default: NULL is "nobody is named", which is what a draft
started from an empty body is, and it is also what every draft that exists before this
migration is. There is no backfill to do and none worth doing — an unnamed seat renders as
"Team {slot}", which is what it was called yesterday.

Why it is not part of the config SNAPSHOT argument: the rest of `draft`'s columns are copied
in so a pick number keeps meaning what it meant (`app.db.models.draft`). Names are the one
thing on the row that is purely cosmetic, which is why `PUT /draft/config` can merge them
mid-draft while the seat I sit in is frozen once a pick has been made.

Pure `add_column` of a nullable column — no constraint, no backfill, no `batch_alter_table`
needed — so it applies unchanged on SQLite and on Postgres, where `make migrate` is still the
acceptance check.

Revision ID: a71f4e0c9d53
Revises: f3a9c41d7b62
Create Date: 2026-09-26 11:04:18.226401

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a71f4e0c9d53"
down_revision: str | Sequence[str] | None = "f3a9c41d7b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("draft", sa.Column("team_names", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema.

    Drops the names and nothing else: every seat goes back to being "Team {slot}", which is
    what it renders as when the column is NULL anyway. The only migration in this project
    whose loss costs nothing a draft depends on.
    """
    op.drop_column("draft", "team_names")
