"""draft and draft pick

Adds the two tables a LIVE draft needs: `draft` (the one active draft, with its config
snapshotted) and `draft_pick` (one row per pick made in it).

Why the config is copied into `draft` rather than read from `Settings` on every request: a
draft is a log of pick NUMBERS, and a pick number only means something under one shape. A
`DRAFT_MY_SLOT` changed mid-draft would silently re-label every pick already made. Snapshotting
`team_count` / `rounds` / `my_slot` / `roster_slots` makes the row self-describing, so it
replays to exactly the state it was in whatever the environment has since become
(`app.draft.session.build_state`). `field_horizon` / `field_source_ids` are part of the same
snapshot one step removed: they decide which consensus the simulated room drafts off, which is
how this draft was MODELLED and not a per-request preference.

Why `field_source_ids` is nullable JSON rather than defaulting to `[]`: NULL means "every
source the horizon offers", which is a different instruction from "no sources at all".

Why one `draft_pick` shape for both kinds of pick: a manual entry and a simulated opponent pick
are the same event — a number, a seat, a player — so they are the same row, distinguished only
by `is_auto`. That is what makes undo one delete of the highest `pick_number` and replay one
ordered scan, whatever made the picks.

Why `player_id` is NOT NULL: a pick that took nobody is a *projection* stepping over my own
seat (`DraftState.pass_pick`), and a projection is never persisted — only made picks are.

Why `(draft_id, pick_number)` is UNIQUE: the log is the draft, so a pick number can exist once
in it. Two rows at pick 19 would make "who was available at 19" a question with two answers.
Named, so the model, this migration and the API message all agree on it; on both dialects it
doubles as the index every read of the log uses.

Why no CHECK on `mode`: the vocabulary ('simulation' | 'manual') is validated at the API edge
against `app.db.models.draft.DRAFT_MODES`, so a third mode later is a constant and a UI chip
rather than a migration on Postgres and a table rebuild on SQLite. Same argument
`master_rank_entry.tag` and `master_tier_break.scope` are made with.

What is deliberately absent: availability. Those percentages are a Monte Carlo over the state
(`app.draft.availability`), recomputed on request; storing them would store a derivation that
goes stale the instant the next pick lands.

Pure `create_table` — nothing existing is altered, no `batch_alter_table` needed — so it
applies unchanged on SQLite (which is what lets the migration test drive it offline) and on
Postgres, where `make migrate` is still the acceptance check.

Revision ID: f3a9c41d7b62
Revises: e5c18b7a2f90
Create Date: 2026-09-25 10:12:44.108371

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3a9c41d7b62"
down_revision: str | Sequence[str] | None = "e5c18b7a2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UNIQUE = "uq_draft_pick_draft_number"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "draft",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_count", sa.Integer(), nullable=False),
        sa.Column("rounds", sa.Integer(), nullable=False),
        sa.Column("my_slot", sa.Integer(), nullable=False),
        sa.Column("roster_slots", sa.JSON(), nullable=False),
        sa.Column("field_horizon", sa.String(length=32), nullable=False),
        # NULL = every source the horizon offers. See the docstring.
        sa.Column("field_source_ids", sa.JSON(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "draft_pick",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("pick_number", sa.Integer(), nullable=False),
        sa.Column("team_slot", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.Integer(), nullable=False),
        sa.Column("is_auto", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        # Deleting the draft takes its log with it: the picks are meaningless without the
        # shape that numbered them, which is the same reason `POST /draft?reset=true` drops
        # them rather than trying to carry them across a reconfigure.
        sa.ForeignKeyConstraint(["draft_id"], ["draft.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_id"], ["player.espn_player_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "pick_number", name=UNIQUE),
    )
    op.create_index(op.f("ix_draft_pick_player_id"), "draft_pick", ["player_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    Drops both tables and the draft on them. Lossy and un-resyncable — unlike the tiers, which
    re-derive from the value gaps, a draft log is a record of something that happened and
    nothing can reconstruct it. Nothing else depends on these tables: the master board's
    draft-mode annotation degrades to "nothing is drafted" when there is no draft, which is
    exactly the state going back past this revision leaves it in.
    """
    op.drop_index(op.f("ix_draft_pick_player_id"), table_name="draft_pick")
    op.drop_table("draft_pick")
    op.drop_table("draft")
