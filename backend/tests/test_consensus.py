"""app.ranking.consensus — the averaging rules, on hand-built sources.

Pure in, pure out: no database, no settings, no player names. Every rule this module is
supposed to enforce (equal weight, the missing-player rule, the spread, the ordering) is
visible in five rows of fixture, which is the point of the engine being pure.
"""

import pytest

from app.ranking import (
    METHOD_PERCENTILE,
    METHOD_RANK,
    Placement,
    RankingSource,
    SourceSpec,
    UnknownMethod,
    consensus_board,
    percentile_for,
)
from app.ranking.sources import KIND_RANKING

POOL = 101  # so percentile_for(rank) is a round number: rank 1 -> 100, rank 51 -> 50.


def source(source_id: str, ranks: dict[int, int]) -> RankingSource:
    """A source that places these player ids at these ranks, over a 101-player pool."""
    return RankingSource(
        spec=SourceSpec(
            id=source_id, label=source_id, kind=KIND_RANKING, source="test", season=2027
        ),
        placements=tuple(
            Placement(player_id=player_id, rank=rank, percentile=percentile_for(rank, POOL))
            for player_id, rank in sorted(ranks.items(), key=lambda item: item[1])
        ),
    )


# Two sources that broadly agree, plus one player each has to itself.
A = source("a", {1: 1, 2: 3, 3: 11, 4: 21})
B = source("b", {1: 5, 2: 1, 3: 41, 5: 31})


def by_name(rows) -> dict[int, object]:
    return {row.player_id: row for row in rows}


# --- the average ------------------------------------------------------------------------------


def test_the_rank_method_averages_places_and_orders_lowest_first():
    rows = consensus_board([A, B], METHOD_RANK)

    assert by_name(rows)[1].consensus == 3.0  # (1 + 5) / 2
    assert by_name(rows)[2].consensus == 2.0  # (3 + 1) / 2
    assert by_name(rows)[3].consensus == 26.0  # (11 + 41) / 2
    # 2.0, 3.0, then 21.0 (A alone), 26.0, 31.0 (B alone).
    assert [row.player_id for row in rows] == [2, 1, 4, 3, 5]


def test_the_percentile_method_averages_pool_positions_and_orders_highest_first():
    rows = consensus_board([A, B], METHOD_PERCENTILE)

    # rank 1 -> 100, rank 5 -> 96; rank 3 -> 98, rank 1 -> 100.
    assert by_name(rows)[1].consensus == pytest.approx(98.0)
    assert by_name(rows)[2].consensus == pytest.approx(99.0)
    # The same order the rank method gives: percentile is that rank restated over one shared
    # denominator, so the two methods only ever disagree about the NUMBERS, never the order.
    assert [row.player_id for row in rows] == [2, 1, 4, 3, 5]


def test_every_selected_source_counts_exactly_once():
    """Equal weight: the same source twice must not become a vote and a half."""
    assert consensus_board([A, B], METHOD_RANK) == consensus_board([A, B, A], METHOD_RANK)


def test_the_order_the_sources_are_passed_in_does_not_change_the_average():
    swapped = {row.player_id: row.consensus for row in consensus_board([B, A], METHOD_RANK)}

    assert swapped == {row.player_id: row.consensus for row in consensus_board([A, B], METHOD_RANK)}


def test_an_unknown_method_raises_rather_than_defaulting_to_one_of_them():
    with pytest.raises(UnknownMethod) as caught:
        consensus_board([A, B], "average")

    assert METHOD_RANK in str(caught.value) and METHOD_PERCENTILE in str(caught.value)


def test_no_sources_is_an_empty_board_not_an_error():
    assert consensus_board([], METHOD_RANK) == []


# --- the missing-player rule ---------------------------------------------------------------------


def test_a_player_missing_from_a_source_is_left_out_of_the_average_not_counted_last():
    """Player 4 is 21st on A and absent from B. His consensus is 21, not (21 + 101) / 2."""
    row = by_name(consensus_board([A, B], METHOD_RANK))[4]

    assert row.consensus == 21.0
    assert row.sources_present == ("a",)
    assert row.sources_missing == ("b",)


def test_the_row_says_which_sources_skipped_him_so_thin_coverage_is_visible():
    """The honest rule's cost: a one-source 21.0 is not a two-source 21.0, and this is how
    you tell them apart."""
    rows = by_name(consensus_board([A, B], METHOD_RANK))

    assert len(rows[1].sources_present) == 2 and rows[1].sources_missing == ()
    assert len(rows[5].sources_present) == 1 and rows[5].sources_missing == ("a",)


def test_a_source_with_no_opinion_has_no_cell_rather_than_a_cell_full_of_nulls():
    row = by_name(consensus_board([A, B], METHOD_RANK))[4]

    assert set(row.cells) == {"a"}
    assert row.cells["a"].rank == 21


def test_one_row_per_player_over_the_union_of_the_selected_sources():
    rows = consensus_board([A, B], METHOD_RANK)

    assert len(rows) == 5
    assert len({row.player_id for row in rows}) == 5
    assert {row.player_id for row in rows} == {1, 2, 3, 4, 5}


def test_a_player_no_selected_source_ranks_is_not_a_row():
    rows = consensus_board([A], METHOD_RANK)

    assert {row.player_id for row in rows} == {1, 2, 3, 4}  # 5 is B's alone


# --- the spread -----------------------------------------------------------------------------------


def test_the_spread_is_the_disagreement_across_the_sources_that_do_rank_him():
    row = by_name(consensus_board([A, B], METHOD_RANK))[3]

    assert row.rank_spread == 30.0  # 41 - 11
    assert row.spread == pytest.approx(30.0)  # and the same 30 places, on the 0-100 scale


def test_the_spread_is_null_below_two_sources_rather_than_zero():
    """One source cannot disagree with itself, and a 0 would paint perfect agreement."""
    row = by_name(consensus_board([A, B], METHOD_RANK))[4]

    assert row.spread is None and row.rank_spread is None


def test_two_sources_that_agree_exactly_have_no_spread_at_all():
    rows = by_name(consensus_board([A, source("c", {1: 1})], METHOD_RANK))

    assert rows[1].spread == 0.0 and rows[1].rank_spread == 0.0


def test_the_spread_does_not_depend_on_which_method_is_selected():
    """It is the disagreement between the sources, not a property of how they were averaged."""
    ranked = {row.player_id: row.spread for row in consensus_board([A, B], METHOD_RANK)}
    pct = {row.player_id: row.spread for row in consensus_board([A, B], METHOD_PERCENTILE)}

    assert ranked == pct


# --- ordering -------------------------------------------------------------------------------------


def test_coverage_breaks_a_tie_before_anything_else_does():
    """Same average, more sources behind it: the better-supported opinion goes first."""
    thin = source("thin", {9: 7})
    rows = consensus_board([source("x", {8: 7}), source("y", {8: 7}), thin], METHOD_RANK)

    assert [row.player_id for row in rows] == [8, 9]


def test_names_break_a_remaining_tie_so_two_identical_requests_agree():
    rows = consensus_board(
        [source("x", {8: 7, 9: 7})], METHOD_RANK, names={8: "Zubac, Ivica", 9: "Adams, Steven"}
    )

    assert [row.player_id for row in rows] == [9, 8]


def test_the_board_is_stable_without_names_too():
    rows = consensus_board([source("x", {9: 7, 8: 7})], METHOD_RANK)

    assert [row.player_id for row in rows] == [8, 9]
