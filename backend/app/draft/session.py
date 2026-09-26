"""The one place a stored draft becomes a runnable one: replay the log, hand back the engine.

`app.draft.field` turns stored OPINION into the field's board. This turns a stored DRAFT — a
`Draft` row and its `DraftPick` log — into a `DraftState` sitting at exactly the point it was
left at. Between the two, the whole engine is reachable from a `Session` and nothing else in
`app.draft` has to know a database exists.

REPLAY, NOT RESTORE. `build_state` does not reconstruct the rosters and the available set from
a snapshot; it walks the log in pick order and calls `apply_pick` on each row, which means the
state you get back was produced by the same code path that produced it in the first place. A
log that has somehow become illegal — a player drafted twice, a pick under the wrong seat, a
pick number past the end of the draft — raises `ValueError` here rather than quietly rehydrating
into a state that disagrees with the rows it came from. That is the honest failure: the picks
are the record, and if they cannot be a draft then nothing computed from them means anything.

WHY THE CONFIG COMES OFF THE ROW and not off `Settings`: a pick number only means something
under one shape, so a draft carries its own (see `app.db.models.draft`). `draft_config` is that
translation, and it is the reason a draft started at slot 2 still replays to slot 2 after
`DRAFT_MY_SLOT` has been changed for next year.

WHY THE UNIVERSE IS THE FIELD'S BOARD: `DraftState` refuses a player nobody ranks, because
availability computed over a board the simulation never draws from is a number about nothing.
It follows that a draft's universe moves when the sources under it move — an import that adds
a hundred names widens it on the next read, and one that drops a source can narrow it. A log
holding a player the narrowed field no longer ranks will refuse to replay, which is loud and
correct: the room being modelled changed, and the draft needs re-creating against the new one.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.draft import Draft, DraftPick
from app.draft.autopick import FieldBoard
from app.draft.config import DraftConfig
from app.draft.field import field_ranks, positions_for
from app.draft.state import DraftState


def draft_config(draft: Draft) -> DraftConfig:
    """The stored config snapshot, as the engine's `DraftConfig`.

    Validation (a seat outside the league, a roster with no slots) happens in
    `DraftConfig.__post_init__` and raises `ValueError` — which for a row that was validated
    on the way in means someone edited the database, and is worth failing on rather than
    running a draft whose seat does not exist.
    """
    return DraftConfig(
        team_count=draft.team_count,
        rounds=draft.rounds,
        roster_slots=dict(draft.roster_slots or {}),
        my_slot=draft.my_slot,
    )


def build_state(db: Session, draft: Draft) -> tuple[DraftState, FieldBoard]:
    """Rebuild this draft's engine state and the field board it was modelled against.

    Two queries for the board (`field_ranks`, `positions_for`) and one for the log, then a
    replay. The `FieldBoard` comes back with it because every caller needs both and building
    one twice per request would sort the board twice — `simulate_availability` and
    `simulate_opponents_until` both take exactly this pair.

    Raises `UnknownHorizon` / `UnknownSources` if the draft's stored field selection no longer
    resolves, and `ValueError` if the stored log cannot be replayed (see the module docstring).
    """
    ranks = field_ranks(db, draft.field_horizon, draft.field_source_ids)
    state = DraftState(draft_config(draft), ranks, positions_for(db, ranks.keys()))
    for pick in _log(db, draft):
        # `team_slot` is passed as the assertion `apply_pick` treats it as: the stored seat has
        # to be the one the snake says owns that pick number, or the log and the shape disagree
        # and this draft cannot be replayed at all.
        state.apply_pick(pick.player_id, team_slot=pick.team_slot)
    return state, FieldBoard.of(ranks)


def _log(db: Session, draft: Draft) -> list[DraftPick]:
    """Every pick in this draft, in pick order — the replay order, stated once.

    Queried rather than read off `draft.picks` so a caller that has just deleted a pick (undo,
    reset) sees the log as the database holds it rather than as a loaded collection remembers
    it.
    """
    return list(
        db.scalars(
            select(DraftPick).where(DraftPick.draft_id == draft.id).order_by(DraftPick.pick_number)
        )
    )
