"""app.draft — the snake, the clock, need, the field's hand, and the availability Monte Carlo.

No database at all. The engine is pure precisely so that a whole simulated draft fits in a
dozen hand-written players and four teams, where every number below is one you can check by
hand — which is the only way to tell a Monte Carlo that is right from one that merely runs.

The db-touching half (the consensus the field drafts off) is `test_draft_field`.
"""

from random import Random

import pytest

from app.config import get_settings
from app.draft import (
    DEFAULT_AUTOPICK_NEED_MULT,
    DEFAULT_AUTOPICK_TEMPERATURE,
    DEFAULT_AUTOPICK_TOP_K,
    DEFAULT_ROSTER_SLOTS,
    DEFAULT_SIM_ITERATIONS,
    DraftConfig,
    DraftState,
    FieldBoard,
    auto_pick,
    candidates,
    fill_roster,
    fills_need,
    pick_weights,
    simulate_availability,
    simulate_opponents_until,
)

# A whole draft on ten players: ids 1..10, ranked 1..10 by the field. Small enough that the
# board runs out mid-draft, which is a state the engine has to survive rather than avoid.
TEN = {player_id: player_id for player_id in range(1, 11)}

# Near-zero temperature: exp(-(rank - best) / 0.01) is e^-100 for the next man up, so the
# field drafts the consensus straight down. A deterministic room is what makes the
# availability assertions below exact numbers instead of confidence intervals.
STRICT = 0.01


def state_for(config: DraftConfig, ranks=TEN, positions=None) -> DraftState:
    return DraftState(config, ranks, positions)


def draft_in_order(state: DraftState, player_ids) -> None:
    """Commit these players, one per pick, in the snake's order."""
    for player_id in player_ids:
        state.apply_pick(player_id)


# --- the shape, and the snake -----------------------------------------------------------------


def test_my_pick_numbers_are_the_snake_our_league_actually_deals():
    config = DraftConfig()

    # Slot 2 of 10: second overall, then a seventeen-pick wait, then a three-pick one, forever.
    assert config.my_pick_numbers[:5] == [2, 19, 22, 39, 42]
    assert len(config.my_pick_numbers) == 20
    assert config.total_picks == 200


def test_a_four_team_three_round_snake_is_the_one_you_can_count_on_your_fingers():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)

    assert list(config.pick_order) == [1, 2, 3, 4, 4, 3, 2, 1, 1, 2, 3, 4]
    assert config.my_pick_numbers == [2, 7, 10]
    assert config.round_of(1) == 1 and config.round_of(5) == 2 and config.round_of(12) == 3


def test_pick_slot_round_trips_against_pick_numbers_for():
    config = DraftConfig()

    for pick_number in range(1, config.total_picks + 1):
        slot = config.pick_slot(pick_number)
        assert pick_number in config.pick_numbers_for(slot)
    # Every seat owns exactly one pick per round, snake or not.
    assert all(len(config.pick_numbers_for(slot)) == 20 for slot in range(1, 11))
    assert config.is_my_pick(19) and not config.is_my_pick(20)


def test_a_pick_number_outside_the_draft_raises_rather_than_wrapping():
    config = DraftConfig()

    with pytest.raises(ValueError, match="outside 1..200"):
        config.pick_slot(201)
    with pytest.raises(ValueError, match="outside 1..200"):
        config.pick_slot(0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"my_slot": 0}, "DRAFT_MY_SLOT"),
        ({"my_slot": 11}, "DRAFT_MY_SLOT"),
        ({"team_count": 1}, "DRAFT_TEAM_COUNT"),
        ({"rounds": 0}, "DRAFT_ROUNDS"),
        ({"roster_slots": {"PG": -1}}, "roster_slots"),
        ({"roster_slots": {"PG": "one"}}, "roster_slots"),
        ({"roster_slots": {}}, "at least one slot"),
    ],
)
def test_a_nonsense_shape_fails_at_construction_naming_the_setting(kwargs, message):
    with pytest.raises(ValueError, match=message):
        DraftConfig(**kwargs)


def test_the_roster_shape_separates_dedicated_slots_from_the_positionless_ones():
    config = DraftConfig()

    assert config.dedicated_counts == {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1}
    assert config.utility_slots == 2
    assert config.bench_slots == 13
    # Twenty slots for twenty rounds, which is why nobody ever has to pick for legality.
    assert config.roster_size == 20 == config.rounds


def test_the_roster_slots_a_caller_passes_cannot_be_mutated_out_from_under_the_draft():
    slots = dict(DEFAULT_ROSTER_SLOTS)
    config = DraftConfig(roster_slots=slots)

    slots["C"] = 99

    assert config.dedicated_counts["C"] == 1


def test_the_engine_defaults_are_the_draft_settings_defaults():
    """The one guard against the duplication `app.draft.config` documents.

    The engine is pure and cannot import `Settings` (it would close a cycle through
    `app.draft.field`), so the numbers live in both places. If someone changes one, this fails.
    """
    settings = get_settings()

    assert DraftConfig.from_settings(settings) == DraftConfig()
    assert settings.draft_autopick_topk == DEFAULT_AUTOPICK_TOP_K
    assert settings.draft_autopick_temperature == DEFAULT_AUTOPICK_TEMPERATURE
    assert settings.draft_autopick_need_mult == DEFAULT_AUTOPICK_NEED_MULT
    assert settings.draft_sim_iterations == DEFAULT_SIM_ITERATIONS


def test_from_settings_takes_the_roster_from_the_league_not_the_environment():
    config = DraftConfig.from_settings(get_settings(), roster_slots={"PG": 2, "UT": 1, "BE": 5})

    assert config.dedicated_counts == {"PG": 2}
    assert config.utility_slots == 1


# --- the state and the clock ------------------------------------------------------------------


def test_a_pick_leaves_the_board_and_advances_the_clock_through_the_snake():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    state = state_for(config)

    assert state.on_the_clock == 1 and state.next_pick_number == 1
    state.apply_pick(3)

    assert not state.is_available(3)
    assert 3 not in state.available
    assert state.on_the_clock == 2 and state.next_pick_number == 2
    assert state.roster(1) == [3]
    assert state.selections[0].pick_number == 1 and state.selections[0].team_slot == 1

    # Through the turn: picks 4 and 5 both belong to slot 4, then it snakes back down.
    draft_in_order(state, [1, 2, 4])
    assert state.on_the_clock == 4
    state.apply_pick(5)
    assert state.roster(4) == [4, 5]
    assert state.on_the_clock == 3


def test_the_state_refuses_a_double_draft_an_unknown_player_and_a_pick_past_the_end():
    config = DraftConfig(team_count=2, rounds=5)  # ten picks over the ten ranked players
    state = state_for(config)
    state.apply_pick(1)

    with pytest.raises(ValueError, match="already been drafted"):
        state.apply_pick(1)
    with pytest.raises(ValueError, match="not in this draft's universe"):
        state.apply_pick(999)
    # Pick 2 belongs to slot 2, and saying otherwise is a desynchronised room, not a pick.
    with pytest.raises(ValueError, match="belongs to team slot 2"):
        state.apply_pick(2, team_slot=1)

    draft_in_order(state, range(2, 11))
    assert state.is_complete
    assert state.on_the_clock is None and state.next_pick_number is None
    with pytest.raises(ValueError, match="draft is complete"):
        state.apply_pick(10)
    with pytest.raises(ValueError, match="draft is complete"):
        state.pass_pick()


def test_copy_is_an_independent_draft():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    state = state_for(config, positions={1: ["PG"], 2: ["C"]})
    state.apply_pick(1)

    fork = state.copy()
    fork.apply_pick(2)

    assert fork.roster(2) == [2] and state.roster(2) == []
    assert state.is_available(2) and not fork.is_available(2)
    assert state.next_pick_number == 2 and fork.next_pick_number == 3
    assert "C" in state.open_dedicated(2) and "C" not in fork.open_dedicated(2)


def test_a_passed_pick_consumes_the_clock_and_touches_nothing_else():
    config = DraftConfig(team_count=4, rounds=3, my_slot=1)
    state = state_for(config)

    passed = state.pass_pick()

    assert passed.player_id is None and passed.team_slot == 1
    assert state.available == frozenset(TEN)
    assert state.roster(1) == []
    assert state.next_pick_number == 2
    # It is a consumed pick number, not a selection — `selections` is what Task 20 persists.
    assert len(state.picks) == 1 and state.selections == ()


# --- need ---------------------------------------------------------------------------------------


def test_open_dedicated_slots_shrink_as_eligible_starters_are_drafted():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(
        config,
        ranks={1: 1, 2: 2, 3: 3, 4: 4},
        positions={1: ["PG"], 2: ["SG"], 3: ["PG"], 4: ["C"]},
    )

    assert state.open_dedicated(1) == {"PG", "SG", "SF", "PF", "C"}
    state.apply_pick(1)  # slot 1 takes the PG

    assert state.open_dedicated(1) == {"SG", "SF", "PF", "C"}
    assert not state.fills_need(1, 3)  # another PG covers nothing new
    assert state.fills_need(1, 4)  # the centre does
    assert state.open_dedicated(2) == {"PG", "SG", "SF", "PF", "C"}  # the other team is untouched


def test_a_multi_position_player_fills_whichever_dedicated_slot_is_open():
    # Both open: the greedy assignment takes PG, the first in DEDICATED_POSITIONS order.
    both = fill_roster(DEFAULT_ROSTER_SLOTS, [["PG", "SG"]])
    assert both.assignments == ("PG",)
    assert both.open_dedicated == {"SG", "SF", "PF", "C"}

    # PG already gone: the same player slots in at SG instead.
    only_sg = fill_roster(DEFAULT_ROSTER_SLOTS, [["PG"], ["PG", "SG"]])
    assert only_sg.assignments == ("PG", "SG")
    assert only_sg.open_dedicated == {"SF", "PF", "C"}


def test_once_the_starting_five_is_covered_nobody_fills_a_need_any_more():
    ledger = fill_roster(DEFAULT_ROSTER_SLOTS, [["PG"], ["SG"], ["SF"], ["PF"], ["C"]])

    assert ledger.open_dedicated == frozenset()
    # UT and the bench take anyone, so there is no positional reason left to prefer anybody.
    for positions in (["PG"], ["C"], ["PG", "SG", "SF", "PF", "C"], []):
        assert not ledger.fills_need(positions)
    assert not fills_need(["C"], ledger.open_dedicated)

    # The next two go to utility, then the rest to the bench.
    ledger.add(["PG"])
    ledger.add(["C"])
    assert ledger.assignments[5:] == ("UT", "UT")
    assert ledger.add(["SF"]) == "BE"


def test_a_player_we_hold_no_positions_for_fills_no_dedicated_slot():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config, ranks={1: 1}, positions={})

    assert not state.fills_need(1, 1)
    state.apply_pick(1)
    assert state.open_dedicated(1) == {"PG", "SG", "SF", "PF", "C"}


def test_positions_are_normalised_before_they_are_matched():
    ledger = fill_roster(DEFAULT_ROSTER_SLOTS, [[" pg ", "PG"]])

    assert ledger.assignments == ("PG",)


# --- the field's hand ---------------------------------------------------------------------------


def test_the_shortlist_is_the_top_k_still_on_the_board():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config)
    draft_in_order(state, [1, 3])

    assert candidates(state, TEN, top_k=4) == [2, 4, 5, 6]
    assert candidates(state, TEN, top_k=99) == [2, 4, 5, 6, 7, 8, 9, 10]
    with pytest.raises(ValueError, match="DRAFT_AUTOPICK_TOPK"):
        candidates(state, TEN, top_k=0)


def test_the_weights_are_the_documented_softmax_with_a_need_multiplier_on_top():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config, positions={1: ["PG"], 2: ["C"]})
    # Slot 1 fills PG at pick 1; slot 2 takes a nobody at picks 2 and 3; back to slot 1 at 4.
    state.apply_pick(1)

    weights = pick_weights(
        state,
        1,
        [2, 3],
        FieldBoard.of(TEN),
        temperature=DEFAULT_AUTOPICK_TEMPERATURE,
        need_mult=DEFAULT_AUTOPICK_NEED_MULT,
    )

    # Player 2 is ranked best of the two (so exp(0) == 1) and fills the open C: 1 * 1.5.
    # Player 3 is one place worse and fills nothing: exp(-1/8).
    assert weights[0] == pytest.approx(1.5)
    assert weights[1] == pytest.approx(0.8824969, abs=1e-6)


@pytest.mark.parametrize(("temperature", "need_mult"), [(0.0, 1.5), (8.0, 0.0)])
def test_nonsense_autopick_dials_fail_loudly(temperature, need_mult):
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config)

    with pytest.raises(ValueError, match="DRAFT_AUTOPICK_"):
        pick_weights(
            state, 1, [1, 2], FieldBoard.of(TEN), temperature=temperature, need_mult=need_mult
        )


def test_auto_pick_is_deterministic_under_a_fixed_random():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config)

    first = [auto_pick(state.copy(), TEN, Random(seed)) for seed in range(5)]
    second = [auto_pick(state.copy(), TEN, Random(seed)) for seed in range(5)]

    assert first == second


def test_the_need_tilt_moves_the_draw_materially_off_the_plain_softmax():
    """A centre one place worse than a guard wins more often once the guard slot is filled.

    Asserted as a rate over two thousand seeded draws rather than as a single pick, because
    the tilt is a thumb on a weighted random and a single pick could not tell it from noise.
    """
    config = DraftConfig(team_count=2, rounds=20)
    ranks = ranks_for(20)
    # A two-team snake runs 1, 2, 2, 1, 1, 2, 2, 1, 1: slot 1 owns picks 1, 4, 5 and 8, which
    # is where its PG, SG, SF and PF land, leaving only C open when it is back up at pick 9.
    # Slot 2's picks (2, 3, 6, 7) are positionless filler so its own needs stay irrelevant.
    positions = {1: ["PG"], 4: ["SG"], 5: ["SF"], 8: ["PF"], 9: ["PG"], 10: ["C"]}
    state = state_for(config, ranks=ranks, positions=positions)

    draft_in_order(state, range(1, 9))
    assert state.on_the_clock == 1
    assert state.open_dedicated(1) == {"C"}

    # The two best left are 9 (a PG: fills nothing now) and 10 (the centre: fills the hole).
    assert candidates(state, ranks, top_k=2) == [9, 10]

    rng = Random(20260925)
    draws = [auto_pick(state, ranks, rng, top_k=2) for _ in range(2000)]
    centre_rate = draws.count(10) / len(draws)

    # Plain softmax would give the worse-ranked centre exp(-1/8) / (1 + exp(-1/8)) = 46.9%.
    # The 1.5x need multiplier lifts it to 1.5 * exp(-1/8) / (1 + 1.5 * exp(-1/8)) = 57.0%.
    assert centre_rate == pytest.approx(0.570, abs=0.03)
    assert centre_rate > 0.52


def test_auto_pick_is_a_no_op_when_the_field_has_nobody_ranked_left():
    config = DraftConfig(team_count=2, rounds=20)
    state = state_for(config)
    draft_in_order(state, list(range(1, 11)))

    assert candidates(state, TEN) == []
    assert auto_pick(state, TEN, Random(0)) is None


def test_auto_pick_is_a_no_op_once_the_draft_is_complete():
    config = DraftConfig(team_count=2, rounds=2)
    state = state_for(config)
    draft_in_order(state, [1, 2, 3, 4])

    assert state.is_complete
    assert auto_pick(state, TEN, Random(0)) is None


def test_simulating_the_room_forward_stops_on_my_clock_and_mutates_the_state():
    config = DraftConfig(team_count=4, rounds=3, my_slot=3)
    state = state_for(config)

    made = simulate_opponents_until(state, TEN, Random(1), temperature=STRICT)

    # Picks 1 and 2 belong to slots 1 and 2; pick 3 is mine, so it stops there.
    assert [pick.pick_number for pick in made] == [1, 2]
    assert [pick.player_id for pick in made] == [1, 2]  # the consensus, straight down
    assert state.on_the_clock == 3 and state.next_pick_number == 3


def test_simulating_the_room_stops_when_the_field_runs_out_of_ranked_players():
    config = DraftConfig(team_count=4, rounds=10, my_slot=2)
    state = state_for(config)

    # stop_slot 99 is a seat no team holds, so nothing but the board running dry can stop it.
    made = simulate_opponents_until(state, TEN, Random(1), stop_slot=99, temperature=STRICT)

    # Ten ranked players against forty picks: it drafts the board dry and halts.
    assert len(made) == 10
    assert state.available == frozenset()
    assert not state.is_complete


# --- availability ---------------------------------------------------------------------------------


def test_availability_is_a_probability_deterministic_under_its_seed():
    config = DraftConfig(team_count=4, rounds=5, my_slot=2)
    state = state_for(config, ranks=ranks_for(40))

    kwargs = {"iterations": 200, "seed": 7}
    first = simulate_availability(state, ranks_for(40), [2, 7], **kwargs)
    second = simulate_availability(state, ranks_for(40), [2, 7], **kwargs)

    assert first == second
    assert all(0.0 <= value <= 1.0 for series in first.values() for value in series.values())
    assert set(first) == set(range(1, 41))
    assert all(list(series) == [2, 7] for series in first.values())


def test_the_consensus_number_one_thins_out_as_my_picks_get_later():
    config = DraftConfig()
    ranks = ranks_for(300)
    state = DraftState(config, ranks)

    result = simulate_availability(
        state, ranks, config.my_pick_numbers[:3], iterations=300, seed=3, candidates=[1, 6, 50]
    )

    # One opponent pick before my pick 2; seventeen more before 19; two more before 22.
    assert result[1][2] > 0.7
    assert result[1][19] < 0.1
    # Non-increasing for everyone: a player gone at 19 is gone at 22, in every iteration.
    for series in result.values():
        values = [series[number] for number in config.my_pick_numbers[:3]]
        assert values == sorted(values, reverse=True)
    # Nobody the field ranks 50th is going in the first eighteen picks.
    assert result[50][2] == 1.0 and result[50][19] == 1.0


def test_my_own_pick_slots_remove_nobody_from_the_board():
    """The modelling choice, asserted: a simulated me would have taken the best player.

    Four teams, three rounds, me at slot 1 — so pick 1 is mine and picks 2-7 are the room's.
    With the field drafting the consensus straight down, six opponent picks take ranks 1-6,
    and rank 7 survives to my pick 8. He would NOT have, had the sim picked for me at 1.
    """
    config = DraftConfig(team_count=4, rounds=3, my_slot=1)
    state = state_for(config)

    result = simulate_availability(state, TEN, [1, 8], iterations=25, seed=0, temperature=STRICT)

    assert result[1][1] == 1.0  # nothing happens before the first pick of the draft
    assert result[7][8] == 1.0  # my pick 1 took nobody, so the room only got through six
    assert result[6][8] == 0.0


def test_targets_already_consumed_are_dropped_and_the_sim_stops_at_the_last_one():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    state = state_for(config)
    draft_in_order(state, [1, 2, 3])  # picks 1-3 committed; my pick 2 is history

    result = simulate_availability(
        state, TEN, [2, 7], iterations=10, seed=0, temperature=STRICT, candidates=[1, 6, 7]
    )

    assert all(list(series) == [7] for series in result.values())
    # Already gone, and reported at zero rather than silently missing.
    assert result[1] == {7: 0.0}
    # Only picks 4-6 were projected (pick 7 is the target, measured before it): ranks 4, 5, 6.
    assert result[6][7] == 0.0 and result[7][7] == 1.0


def test_with_no_upcoming_targets_every_candidate_comes_back_with_an_empty_series():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    state = state_for(config)
    draft_in_order(state, [1, 2, 3])  # my pick 2 is already history

    result = simulate_availability(state, TEN, [2], iterations=10, candidates=[4, 5])

    assert result == {4: {}, 5: {}}


def test_candidates_default_to_the_ranked_players_still_on_the_board():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    # Player 11 is in the universe but the field has no rank for him, so he is never tracked
    # and never drafted by the room.
    state = DraftState(config, list(range(1, 12)))
    state.apply_pick(1)

    result = simulate_availability(state, TEN, [7], iterations=10, seed=0)

    assert set(result) == set(range(2, 11))


def test_a_target_pick_outside_the_draft_and_a_zero_iteration_run_both_raise():
    config = DraftConfig(team_count=4, rounds=3, my_slot=2)
    state = state_for(config)

    with pytest.raises(ValueError, match="outside 1..12"):
        simulate_availability(state, TEN, [13], iterations=5)
    with pytest.raises(ValueError, match="DRAFT_SIM_ITERATIONS"):
        simulate_availability(state, TEN, [7], iterations=0)


def ranks_for(count: int) -> dict[int, int]:
    """ids 1..count, ranked 1..count — the field and the universe in one dict."""
    return {player_id: player_id for player_id in range(1, count + 1)}
