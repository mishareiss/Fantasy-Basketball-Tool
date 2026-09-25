"""app.draft.field — the one module in the draft engine that opens a database.

It is deliberately thin: `load_catalog` -> `catalog.select` -> `consensus_positions`, the same
path `GET /board/consensus` and the master board's reference column already take. So what
these tests assert is not a ranking — `test_consensus` and `test_ranking_sources` own that —
but that the field the simulation drafts off is *the same board*, that a source subset really
selects, and that a typo'd source id is refused rather than quietly averaged away.
"""

import pytest
from sqlalchemy import select

from app.db.models import Player
from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.draft import DraftConfig, DraftState, UnknownSources, field_ranks, positions_for
from app.ranking import consensus_positions, load_catalog
from app.valuation import HORIZON_DYNASTY

PROJECTION = "projection:espn"
ADP = "adp:espn"


@pytest.fixture
def names(db):
    return {
        player_id: full_name
        for player_id, full_name in db.execute(
            select(Player.espn_player_id, Player.full_name)
        ).all()
    }


def test_the_field_is_the_consensus_board_read_as_an_order(db, synced, names):
    catalog = load_catalog(db, HORIZON_DYNASTY)
    expected = consensus_positions(list(catalog.sources), names=names)

    ranks = field_ranks(db, HORIZON_DYNASTY)

    assert ranks
    assert ranks == expected
    # 1-based and dense: it is a place on a board, not a score.
    assert sorted(ranks.values()) == list(range(1, len(ranks) + 1))


def test_naming_a_subset_of_sources_changes_the_field(db, synced):
    everything = field_ranks(db, HORIZON_DYNASTY)
    adp_only = field_ranks(db, HORIZON_DYNASTY, [ADP])
    projection_only = field_ranks(db, HORIZON_DYNASTY, [PROJECTION])

    # A room drafting ADP is a different room from one drafting the projection, and the whole
    # reason the subset is a parameter rather than a constant.
    assert adp_only != projection_only
    assert everything != adp_only
    assert set(adp_only) <= set(everything)


def test_an_imported_list_joins_the_field_and_moves_it(db, synced, make_ranking_set):
    top = list(
        db.scalars(select(Player.espn_player_id).order_by(Player.espn_player_id)).fetchmany(6)
    )
    before = field_ranks(db, HORIZON_DYNASTY)
    ranking_set = make_ranking_set(
        "Dizzle Dynasty", TAG_DYNASTY, {player_id: place for place, player_id in enumerate(top, 1)}
    )

    after = field_ranks(db, HORIZON_DYNASTY)
    source_id = f"ranking:{ranking_set.id}"

    assert after != before
    assert field_ranks(db, HORIZON_DYNASTY, [source_id]) == dict(
        zip(top, range(1, len(top) + 1), strict=True)
    )


def test_an_unknown_source_id_is_refused_rather_than_silently_skipped(db, synced):
    with pytest.raises(UnknownSources, match="ranking:9999"):
        field_ranks(db, HORIZON_DYNASTY, [PROJECTION, "ranking:9999"])


def test_an_unknown_horizon_raises_the_same_error_the_board_routes_turn_into_a_400(db, synced):
    from app.ranking import UnknownHorizon

    with pytest.raises(UnknownHorizon):
        field_ranks(db, "next_tuesday")


def test_an_empty_database_gives_an_empty_field_rather_than_an_error(db):
    assert field_ranks(db, HORIZON_DYNASTY) == {}


def test_positions_come_back_as_lists_including_for_players_espn_lists_none_for(db, synced):
    ranks = field_ranks(db, HORIZON_DYNASTY)

    positions = positions_for(db, ranks)

    assert set(positions) == set(ranks)
    assert all(isinstance(value, list) for value in positions.values())
    assert any(value for value in positions.values())
    assert positions_for(db, []) == {}
    assert set(positions_for(db)) >= set(ranks)


def test_the_field_and_the_positions_are_exactly_what_a_draft_state_is_built_from(db, synced):
    """The seam Task 20 will use: two queries in, a runnable draft out."""
    ranks = field_ranks(db, HORIZON_DYNASTY)
    config = DraftConfig()

    state = DraftState(config, ranks, positions_for(db, ranks))

    assert len(state.available) == len(ranks)
    assert state.on_the_clock == 1
    best = min(ranks, key=lambda player_id: ranks[player_id])
    state.apply_pick(best)
    assert not state.is_available(best)
