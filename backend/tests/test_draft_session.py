"""app.draft.session — the seam where a stored draft becomes a runnable one.

One assertion, really, and it is the one the whole feature stands on: a draft REPLAYED from its
pick rows is the same draft as one built by applying those picks directly. If that is true then
nothing downstream has to care whether the page was refreshed, and every availability number is
computed against the picks that actually happened. The rest of the file is the ways it can
legitimately fail — a log that isn't a draft — failing loudly rather than rehydrating into a
state that disagrees with the rows it came from.

The endpoints over this live in `test_api_draft`; nothing here goes through HTTP.
"""

import pytest
from sqlalchemy import select

from app.db.models import Player
from app.db.models.draft import MODE_SIMULATION, Draft, DraftPick
from app.draft import DraftConfig, DraftState, build_state, draft_config, field_ranks, positions_for
from app.valuation import HORIZON_DYNASTY


@pytest.fixture
def draft(db) -> Draft:
    """The draft as `POST /draft` stores it: our shape, snapshotted, and no picks yet."""
    config = DraftConfig()
    row = Draft(
        team_count=config.team_count,
        rounds=config.rounds,
        my_slot=config.my_slot,
        roster_slots=dict(config.roster_slots),
        field_horizon=HORIZON_DYNASTY,
        field_source_ids=None,
        mode=MODE_SIMULATION,
    )
    db.add(row)
    db.commit()
    return row


def top_of_the_field(db, count: int) -> list[int]:
    """The first `count` players on the field's board — who a draft actually takes."""
    ranks = field_ranks(db, HORIZON_DYNASTY)
    return sorted(ranks, key=lambda player_id: ranks[player_id])[:count]


def log(db, draft: Draft, player_ids: list[int], *, auto_from: int = 99) -> None:
    """Store `player_ids` as picks 1..n in snake order, the way the endpoints do."""
    config = draft_config(draft)
    for number, player_id in enumerate(player_ids, start=1):
        db.add(
            DraftPick(
                draft_id=draft.id,
                pick_number=number,
                team_slot=config.pick_slot(number),
                player_id=player_id,
                is_auto=number >= auto_from,
            )
        )
    db.commit()


def test_a_stored_log_replays_to_the_same_state_as_applying_the_picks_directly(db, synced, draft):
    """Acceptance criterion 6, and the reason this module exists."""
    taken = top_of_the_field(db, 7)
    log(db, draft, taken)

    replayed, board = build_state(db, draft)

    ranks = field_ranks(db, HORIZON_DYNASTY)
    direct = DraftState(DraftConfig(), ranks, positions_for(db, ranks))
    for player_id in taken:
        direct.apply_pick(player_id)

    assert replayed.selections == direct.selections
    assert replayed.available == direct.available
    assert replayed.on_the_clock == direct.on_the_clock == 8
    assert [replayed.roster(slot) for slot in range(1, 11)] == [
        direct.roster(slot) for slot in range(1, 11)
    ]
    assert [replayed.open_dedicated(slot) for slot in range(1, 11)] == [
        direct.open_dedicated(slot) for slot in range(1, 11)
    ]
    # The board comes back with the state because every caller needs both, and it is the same
    # field `field_ranks` builds — not a second ranking assembled here.
    assert board.ranks == ranks


def test_an_empty_log_replays_to_a_draft_that_has_not_started(db, synced, draft):
    state, board = build_state(db, draft)

    assert state.selections == ()
    assert state.next_pick_number == 1
    assert state.on_the_clock == 1
    assert len(state.available) == len(board.ranks) == len(field_ranks(db, HORIZON_DYNASTY))


def test_the_replay_is_in_pick_order_whatever_order_the_rows_were_written_in(db, synced, draft):
    """Ordered by `pick_number`, not by insertion: who was available when depends on it."""
    taken = top_of_the_field(db, 4)
    config = draft_config(draft)
    for number, player_id in reversed(list(enumerate(taken, start=1))):
        db.add(
            DraftPick(
                draft_id=draft.id,
                pick_number=number,
                team_slot=config.pick_slot(number),
                player_id=player_id,
            )
        )
    db.commit()

    state, _ = build_state(db, draft)

    assert [pick.player_id for pick in state.selections] == taken


def test_the_config_comes_off_the_row_and_not_off_the_environment(db, synced):
    """A draft carries its own shape, so a `DRAFT_*` changed later cannot re-label its picks."""
    row = Draft(
        team_count=4,
        rounds=3,
        my_slot=4,
        roster_slots={"PG": 1, "C": 1, "BE": 1},
        field_horizon=HORIZON_DYNASTY,
        mode=MODE_SIMULATION,
    )
    db.add(row)
    db.commit()

    config = draft_config(row)
    state, _ = build_state(db, row)

    assert (config.team_count, config.rounds, config.my_slot) == (4, 3, 4)
    assert config.my_pick_numbers == [4, 5, 12]
    assert state.config.total_picks == 12
    # Nothing here is `DraftConfig()`'s 10x20 default, which is what the environment would say.
    assert config != DraftConfig()


def test_a_log_that_is_not_a_draft_refuses_to_replay_rather_than_inventing_a_state(
    db, synced, draft
):
    """A player drafted twice is not a draft. Loud, because the picks are the record."""
    taken = top_of_the_field(db, 2)
    log(db, draft, [taken[0], taken[0]])

    with pytest.raises(ValueError, match="already been drafted"):
        build_state(db, draft)


def test_a_pick_stored_under_the_wrong_seat_refuses_to_replay(db, synced, draft):
    """`team_slot` is replayed as the assertion `apply_pick` treats it as, not as a hint."""
    taken = top_of_the_field(db, 1)
    db.add(
        DraftPick(draft_id=draft.id, pick_number=1, team_slot=7, player_id=taken[0]),
    )
    db.commit()

    with pytest.raises(ValueError, match="belongs to team slot 1"):
        build_state(db, draft)


def test_a_player_the_field_no_longer_ranks_refuses_to_replay(db, synced, draft):
    """The narrowing case: the room being modelled changed under a draft in progress.

    `app.api.draft` turns this into a 409 pointing at `POST /draft?reset=true`, because a draft
    whose universe no longer contains a player it took cannot be read as anything.
    """
    nobody = db.scalar(
        select(Player.espn_player_id).where(
            Player.espn_player_id.not_in(field_ranks(db, HORIZON_DYNASTY).keys())
        )
    )
    if nobody is None:
        nobody = 999999
        db.add(Player(espn_player_id=nobody, full_name="Nobody At All", positions=["SF"]))
        db.commit()
    log(db, draft, [nobody])

    with pytest.raises(ValueError, match="not in this draft's universe"):
        build_state(db, draft)


def test_the_auto_flag_is_carried_on_the_rows_and_not_by_the_state(db, synced, draft):
    """Replay treats a simulated pick and a typed one identically — `is_auto` is for display."""
    taken = top_of_the_field(db, 4)
    log(db, draft, taken, auto_from=3)

    state, _ = build_state(db, draft)
    stored = list(
        db.scalars(
            select(DraftPick).where(DraftPick.draft_id == draft.id).order_by(DraftPick.pick_number)
        )
    )

    assert [pick.is_auto for pick in stored] == [False, False, True, True]
    assert [pick.player_id for pick in state.selections] == taken
