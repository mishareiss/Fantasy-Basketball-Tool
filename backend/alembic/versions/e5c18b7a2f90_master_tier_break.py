"""master tier break

Adds `master_tier_break`: where OUR board breaks into tiers, stored as cut ranks per scope.

Why a table with no `player_id` in it: a tier here is a RANK BAND, not a set of players. A row
says "a new tier starts at rank 12 of the point guards". That is what lets the tiers reflow for
free when the board is dragged — a player who moves up into the tier-1 band simply is tier 1,
with nothing written. A tier column on `master_rank_entry` would have to be rewritten on every
reorder, and the rewrite has no correct answer (see `app.db.models.master_tier`).

Why `(scope, cut_rank)` is UNIQUE: a divider is a slot, and two rows in one slot would be one
boundary counted twice. Named, so the model, this migration and the 422 all agree on it.

Why cut ranks are scope-relative: in 'overall' a cut rank is a board rank; in 'PG' it is a rank
among the point guards on the board. That is the only reading under which "tier 2 point guards"
means anything.

Why no foreign key and no CHECK on `scope`: there is nothing to point a key at but a position
in a list, and the scope vocabulary is validated at the API edge against
`app.db.models.master_tier.TIER_SCOPES` — so adding a combined 'G'/'F' scope later is a
constant and a UI chip rather than a migration on Postgres and a table rebuild on SQLite. Same
argument `master_rank_entry.tag` is made with.

Pure `create_table` — nothing existing is altered, no `batch_alter_table` needed — so it
applies unchanged on SQLite (which is what lets the migration test drive it offline) and on
Postgres, where `make migrate` is still the acceptance check.

Revision ID: e5c18b7a2f90
Revises: a3f27c91b054
Create Date: 2026-09-19 09:41:07.512204

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5c18b7a2f90"
down_revision: str | Sequence[str] | None = "a3f27c91b054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE = "uq_master_tier_break_scope_cut"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "master_tier_break",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("cut_rank", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "cut_rank", name=UNIQUE),
    )
    op.create_index(
        op.f("ix_master_tier_break_scope"), "master_tier_break", ["scope"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema.

    Drops the table and every divider on it. Lossy, but mildly: unlike the board's own ranks,
    tags and notes, tiers RE-DERIVE. `GET /master/board` seeds any scope it finds no cuts for
    from the value gaps, so going back past this revision costs the hand-adjusted boundaries
    and nothing else — the board comes back tiered, just tiered the way the values would have
    cut it rather than the way Misha did.
    """
    op.drop_index(op.f("ix_master_tier_break_scope"), table_name="master_tier_break")
    op.drop_table("master_tier_break")
