"""adp entry season

Puts `season` on `adp_entry` and re-keys the table from (player, source) to
(player, source, season).

Why: dynasty value is a trend, and ADP is the market half of it. Keyed on (player, source)
alone, every August's sync overwrote last August's answer — so "the room has moved 30 picks on
this 22-year-old" was unanswerable by construction. Keyed with the season, each year's read
survives beside the others.

Existing rows are backfilled before NOT NULL is enforced. Every `adp_entry` row today came
from `sync_adp` against the single configured ESPN season, so `Settings.espn_season` is the
correct season for all of them; the migration refuses to run rather than guessing if that is
unset and rows exist.

Written with `batch_alter_table` so it applies on SQLite as well as Postgres — the migration
test drives it against a throwaway SQLite file, which is what makes the backfill itself
testable offline.

Revision ID: 637150ee8d91
Revises: b46374371451
Create Date: 2026-08-23 21:58:41.723049

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.config import get_settings

# revision identifiers, used by Alembic.
revision: str = "637150ee8d91"
down_revision: str | Sequence[str] | None = "b46374371451"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_UNIQUE = "uq_adp_entry_player_source"
NEW_UNIQUE = "uq_adp_entry_player_source_season"


def _backfill_season() -> int:
    """Stamp the configured ESPN season onto every pre-existing row. Returns how many."""
    bind = op.get_bind()
    pending = bind.scalar(sa.text("SELECT count(*) FROM adp_entry WHERE season IS NULL")) or 0
    if not pending:
        return 0

    season = get_settings().espn_season
    if season is None:
        raise RuntimeError(
            f"{pending} adp_entry rows need a season and ESPN_SEASON is unset. Set ESPN_SEASON "
            "to the season those rows were synced for (or delete them — they re-sync in a "
            "minute) and run the migration again."
        )
    bind.execute(
        sa.text("UPDATE adp_entry SET season = :season WHERE season IS NULL"), {"season": season}
    )
    return pending


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("adp_entry", sa.Column("season", sa.Integer(), nullable=True))
    _backfill_season()
    with op.batch_alter_table("adp_entry") as batch:
        batch.alter_column("season", existing_type=sa.Integer(), nullable=False)
        batch.drop_constraint(OLD_UNIQUE, type_="unique")
        batch.create_unique_constraint(NEW_UNIQUE, ["player_id", "source", "season"])


def downgrade() -> None:
    """Downgrade schema.

    The old key holds one row per (player, source), so any extra seasons have to go: the
    newest survives, the older ones are dropped. That is lossy on purpose — there is nowhere
    to put them in the old shape — and it is the reason to think twice before downgrading past
    this once a second season of ADP exists. ESPN ADP re-syncs in a minute; imported ADP does
    not, so export it first if it matters.
    """
    op.execute(
        sa.text(
            "DELETE FROM adp_entry WHERE id NOT IN ("
            "  SELECT id FROM ("
            "    SELECT id, row_number() OVER ("
            "      PARTITION BY player_id, source ORDER BY season DESC, id DESC"
            "    ) AS rn FROM adp_entry"
            "  ) ranked WHERE rn = 1"
            ")"
        )
    )
    with op.batch_alter_table("adp_entry") as batch:
        batch.drop_constraint(NEW_UNIQUE, type_="unique")
        batch.create_unique_constraint(OLD_UNIQUE, ["player_id", "source"])
        batch.drop_column("season")
