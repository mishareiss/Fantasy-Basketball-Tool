"""player alias match metadata

Records *how* a `player_alias` row was arrived at: `confidence` (1.0 for an exact or hand-made
match, the similarity score for a fuzzy one) and `match_method` (alias / exact / normalized /
fuzzy / manual). Without them every alias looks equally trustworthy, and a shaky 0.89
auto-match is indistinguishable from one a human confirmed.

Additive and nullable — the aliases written before this migration simply have no provenance.
The (source, source_name) unique constraint is untouched.

Revision ID: b46374371451
Revises: 85e6259342d9
Create Date: 2026-08-23 20:11:18.005487

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b46374371451"
down_revision: str | Sequence[str] | None = "85e6259342d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("player_alias", sa.Column("confidence", sa.Float(), nullable=True))
    op.add_column("player_alias", sa.Column("match_method", sa.String(length=24), nullable=True))


def downgrade() -> None:
    """Downgrade schema (drops the provenance columns; the aliases themselves survive)."""
    op.drop_column("player_alias", "match_method")
    op.drop_column("player_alias", "confidence")
