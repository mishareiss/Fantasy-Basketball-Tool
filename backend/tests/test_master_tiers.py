"""The tier arithmetic, on its own, with no database anywhere near it.

`app.ranking.tiers` is two halves and this file is the first one: cut ranks in, tier numbers
out, plus the two rules that let a hand-ordered board be tiered at all — the descending
envelope and the valueless carry. Every assertion here is about numbers, so a failure names a
rule rather than an endpoint. The other half (seed-on-read, storage, the API) is in
`test_api_master_tiers`.
"""

import pytest

from app.ranking.tiers import (
    POSITIONS,
    BadCutRanks,
    UnknownScope,
    require_scope,
    scope_orders,
    seed_cuts,
    tiers_for,
    validate_cuts,
    value_envelope,
)
from app.valuation import TierParams

# Deliberately loose so a short hand-written list actually breaks somewhere: the real dials
# (TIER_POOL=150) would put every one of these boards inside one pool with a median gap
# computed off a handful of numbers.
PARAMS = TierParams(gap_multiple=2.0, min_size=2, max_tiers=15, pool=150)


# --- cut ranks -> tiers -----------------------------------------------------------------------


def test_a_tier_is_how_many_cuts_are_at_or_above_your_rank():
    """The whole mapping, in one assertion: [1, 4, 6] over 7 players is 1-3, 4-5, 6-7."""
    assert tiers_for([1, 4, 6], 7) == [1, 1, 1, 2, 2, 3, 3]


def test_one_cut_is_one_undivided_tier():
    assert tiers_for([1], 5) == [1, 1, 1, 1, 1]


def test_a_board_of_nobody_has_no_tiers():
    assert tiers_for([1], 0) == []


def test_cuts_past_the_end_of_the_board_band_nobody():
    """Only reachable from a hand-edited database; it must not produce a tier of zero players."""
    assert tiers_for([1, 3, 99], 4) == [1, 1, 2, 2]


def test_everyone_below_the_last_cut_is_in_the_bottom_tier():
    """Bands cover the whole board: "below the tiered pool" is the last tier, not no tier."""
    assert tiers_for([1, 3], 6)[-1] == 2


# --- the envelope: a hand-ordered board is not sorted by value --------------------------------


def test_the_envelope_is_the_running_minimum_so_it_can_always_be_tiered():
    """A player Misha promoted above better-valued players flattens the envelope, not breaks it."""
    assert value_envelope([10.0, 4.0, 9.0, 8.0, 2.0]) == [10.0, 4.0, 4.0, 4.0, 2.0]


def test_a_descending_board_is_its_own_envelope():
    assert value_envelope([10.0, 8.0, 3.0]) == [10.0, 8.0, 3.0]


def test_a_valueless_player_carries_the_value_above_him_rather_than_dropping_to_zero():
    """The carry rule. A player we cannot price is not a player we have priced at nothing."""
    assert value_envelope([10.0, None, 9.0, None]) == [10.0, 10.0, 9.0, 9.0]


def test_a_valueless_player_at_the_very_top_carries_from_the_first_price_there_is():
    """Nothing above him to carry down, so the top of the board is flat instead of a cliff."""
    assert value_envelope([None, None, 8.0, 3.0]) == [8.0, 8.0, 8.0, 3.0]


def test_a_board_nobody_can_be_priced_on_is_flat_rather_than_an_error():
    assert value_envelope([None, None, None]) == [0.0, 0.0, 0.0]


# --- seeding ---------------------------------------------------------------------------------


def test_a_seed_opens_a_tier_where_the_value_cliffs():
    """Three clear bands with two chasms between them, and the seed finds both."""
    values = [100.0, 98.0, 96.0, 50.0, 48.0, 46.0, 10.0, 8.0, 6.0]

    assert seed_cuts(values, PARAMS) == [1, 4, 7]


def test_the_seed_always_starts_at_one_because_tier_one_starts_at_the_top():
    assert seed_cuts([10.0, 9.0, 8.0], PARAMS)[0] == 1


def test_a_board_with_no_cliffs_in_it_seeds_one_tier():
    assert seed_cuts([10.0, 9.0, 8.0, 7.0, 6.0], PARAMS) == [1]


def test_an_empty_scope_seeds_nothing():
    assert seed_cuts([], PARAMS) == []


def test_a_valueless_player_never_opens_a_tier_of_his_own():
    """The carry rule, end to end: he lands in the band around him rather than stranded."""
    values = [100.0, 98.0, None, 96.0, 50.0, 48.0]

    cuts = seed_cuts(values, PARAMS)

    assert cuts == [1, 5]
    # Third on the board, and in the same tier as the players either side of him.
    assert tiers_for(cuts, len(values))[:4] == [1, 1, 1, 1]


def test_a_run_of_valueless_players_is_still_one_carried_band():
    values = [100.0, None, None, None, 98.0, 40.0, 38.0]

    assert tiers_for(seed_cuts(values, PARAMS), len(values)) == [1, 1, 1, 1, 1, 2, 2]


def test_a_board_ordered_against_its_values_is_tiered_by_the_envelope_not_rejected():
    """`assign_tiers` refuses a non-descending list; the envelope is why this never reaches it."""
    values = [100.0, 30.0, 99.0, 98.0, 5.0]

    cuts = seed_cuts(values, PARAMS)

    assert cuts[0] == 1
    # The promoted pair sit in the band their slot fell into, not in one of their own.
    assert len(tiers_for(cuts, len(values))) == 5


def test_players_past_the_tiered_pool_extend_the_last_band_rather_than_leaving_it():
    """`assign_tiers` returns None below TIER_POOL; on a board of bands that is the bottom tier."""
    small = TierParams(gap_multiple=2.0, min_size=2, max_tiers=15, pool=4)
    values = [100.0, 98.0, 50.0, 48.0, 5.0, 3.0]

    tiers = tiers_for(seed_cuts(values, small), len(values))

    assert tiers == [1, 1, 2, 2, 2, 2]


# --- validation ------------------------------------------------------------------------------


def test_a_valid_set_of_dividers_comes_back_as_a_tuple():
    assert validate_cuts([1, 4, 9], 20) == (1, 4, 9)


def test_dividers_that_do_not_start_at_one_are_refused():
    with pytest.raises(BadCutRanks, match="must begin with 1"):
        validate_cuts([4, 9], 20)


def test_an_empty_list_of_dividers_is_refused_on_a_board_that_has_players():
    with pytest.raises(BadCutRanks, match="must begin with 1"):
        validate_cuts([], 20)


def test_dividers_out_of_order_are_refused():
    with pytest.raises(BadCutRanks, match="strictly increasing"):
        validate_cuts([1, 9, 4], 20)


def test_two_dividers_in_one_slot_are_refused():
    with pytest.raises(BadCutRanks, match="strictly increasing"):
        validate_cuts([1, 4, 4], 20)


def test_a_divider_past_the_end_of_the_board_is_refused_and_says_how_big_it_is():
    with pytest.raises(BadCutRanks, match=r"\[40\].*20 players"):
        validate_cuts([1, 4, 40], 20)


def test_a_divider_at_rank_zero_or_below_is_refused():
    """Unreachable past the "starts at 1, strictly increasing" pair, but refused either way."""
    with pytest.raises(BadCutRanks):
        validate_cuts([1, 0], 20)
    with pytest.raises(BadCutRanks):
        validate_cuts([0, 1], 20)


def test_an_empty_scope_takes_an_empty_list_and_nothing_else():
    assert validate_cuts([], 0) == ()
    with pytest.raises(BadCutRanks, match="no ranked players"):
        validate_cuts([1], 0)


# --- scopes ----------------------------------------------------------------------------------


def test_the_scopes_are_the_board_plus_one_per_position():
    assert POSITIONS == ("PG", "SG", "SF", "PF", "C")


def test_a_scope_name_is_normalized_rather_than_being_case_sensitive():
    assert require_scope("pg") == "PG"
    assert require_scope(" OVERALL ") == "overall"


def test_a_scope_we_do_not_have_is_refused_naming_the_ones_we_do():
    with pytest.raises(UnknownScope, match="'PG'"):
        require_scope("guard")


def test_each_position_sub_order_is_the_board_filtered_in_board_order():
    ranked = [(1, ["PG"]), (2, ["C"]), (3, ["PG", "SG"]), (4, [])]

    orders = scope_orders(ranked)

    assert orders["overall"] == [1, 2, 3, 4]
    assert orders["PG"] == [1, 3]
    assert orders["SG"] == [3]
    assert orders["PF"] == []


def test_a_player_listed_at_two_positions_is_in_both_sub_orders():
    orders = scope_orders([(1, ["PF", "C"])])

    assert orders["PF"] == [1] and orders["C"] == [1]


def test_a_position_sub_order_is_tiered_on_its_own_ranks_not_the_boards():
    """The eighth-best point guard is at PG-rank 8 whatever his overall rank is."""
    orders = scope_orders(
        [(player_id, ["PG"] if player_id % 2 else ["C"]) for player_id in range(1, 9)]
    )

    assert orders["PG"] == [1, 3, 5, 7]
    assert tiers_for([1, 3], len(orders["PG"])) == [1, 1, 2, 2]
