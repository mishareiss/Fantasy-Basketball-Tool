"""ranking set horizon

Puts `horizon` ('dynasty' | 'redraft') on `ranking_set` and re-keys the table from
(source, name, season) to (source, name, season, horizon).

Why the column: a ranking is rank-only. Unlike a projection or a market line it carries no
production numbers, so nothing downstream can age-adjust it — there is no arithmetic that
turns Yahoo's redraft Top 200 into a dynasty board. The only moment the answer is known is
the import, so the list declares it there and we store what it declared.

Why it joins the key: the same source publishes both boards, under the same name, for the
same season. Keyed without the horizon, importing the dynasty list would silently *replace*
the redraft one — wholesale, since that is how `app.ingest.ranking` writes — and the entries
it ate would be gone.

Existing rows are backfilled to 'redraft' before NOT NULL is enforced. That is the honest
default rather than a guess: near enough all published rank lists are redraft (FEATURE_SPEC
3 makes the same assumption about consensus ADP), and a set that was actually a dynasty board
is re-imported in one call — rankings are pasted files, and re-importing one is the cheap fix.

Written with `batch_alter_table` so it applies on SQLite as well as Postgres, which is what
lets the migration test drive it offline; Postgres remains the acceptance check via
`make migrate`.

Revision ID: c41d9a7e5b30
Revises: b9e3dada060f
Create Date: 2026-09-02 09:14:02.118904

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c41d9a7e5b30"
down_revision: str | Sequence[str] | None = "b9e3dada060f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIQUE = "uq_ranking_set_source_name_season"
NEW_UNIQUE = "uq_ranking_set_source_name_season_horizon"

# What a set imported before this migration is assumed to be. See the module docstring.
BACKFILL_HORIZON = "redraft"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("ranking_set", sa.Column("horizon", sa.String(length=16), nullable=True))
    op.execute(
        sa.text("UPDATE ranking_set SET horizon = :horizon WHERE horizon IS NULL").bindparams(
            horizon=BACKFILL_HORIZON
        )
    )
    with op.batch_alter_table("ranking_set") as batch:
        batch.alter_column("horizon", existing_type=sa.String(length=16), nullable=False)
        batch.drop_constraint(OLD_UNIQUE, type_="unique")
        batch.create_unique_constraint(NEW_UNIQUE, ["source", "name", "season", "horizon"])


def downgrade() -> None:
    """Downgrade schema.

    The old key holds one set per (source, name, season), so a source that published both
    horizons has to lose one: the most recently imported survives and the other is deleted,
    entries and all (the FK cascades). Lossy on purpose — there is nowhere to put the second
    in the old shape — and a ranking is a pasted file rather than something that re-syncs, so
    export anything that matters before going back past this.
    """
    op.execute(
        sa.text(
            "DELETE FROM ranking_set WHERE id NOT IN ("
            "  SELECT id FROM ("
            "    SELECT id, row_number() OVER ("
            "      PARTITION BY source, name, season ORDER BY as_of DESC, id DESC"
            "    ) AS rn FROM ranking_set"
            "  ) ranked WHERE rn = 1"
            ")"
        )
    )
    # Explicit, rather than trusting the FK's ON DELETE CASCADE: SQLite enforces foreign keys
    # only when `PRAGMA foreign_keys=ON`, which is off by default, so the same downgrade would
    # leave orphaned entries there and clean ones on Postgres.
    op.execute(
        sa.text(
            "DELETE FROM ranking_entry WHERE ranking_set_id NOT IN (SELECT id FROM ranking_set)"
        )
    )
    with op.batch_alter_table("ranking_set") as batch:
        batch.drop_constraint(NEW_UNIQUE, type_="unique")
        batch.create_unique_constraint(OLD_UNIQUE, ["source", "name", "season"])
        batch.drop_column("horizon")
