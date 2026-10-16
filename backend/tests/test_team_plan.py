"""The waiver plan's pure engine: moves, chains, the simulation, the Plan lineup and every
warning. No database. Day 1 of 2027 is Tue 10/20/2026, so sp 2 is Wed 10/21, sp 3 Thu 10/22.
"""

import subprocess
import sys
from datetime import UTC, datetime, time

import pytest

from app.db.models import PLAN_DONE, PLAN_PLANNED, PLAN_SKIPPED
from app.team import plan
from app.team.plan import (
    Move,
    PlanContext,
    acquisition_tallies,
    effective_pickup,
    pickup_alternatives,
    plan_lineup,
    resolve_drop,
    simulate,
    slot_substitutions,
    warnings,
)
from tests.conftest import SEASON

PG, SG, SF, PF, C, UT, BE, IR = 0, 1, 2, 3, 4, 11, 12, 13

# My actual roster, and players I might pick up.
X1, X2, X3 = 1, 2, 3
A, B, C_, D = 101, 102, 103, 104
NAMES = {X1: "Xavier One", X2: "Xander Two", X3: "Jerami Grant", A: "Alpha", B: "Bam", C_: "Cole"}
ROSTER = (X1, X2, X3)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def ctx(**overrides) -> PlanContext:
    values = {"season": SEASON, "today_sp": 1, "names": NAMES, "waiver_hours": 24}
    values.update(overrides)
    return PlanContext(**values)


def run(moves, today=1, through=10, **context):
    sim = simulate(ROSTER, moves, today, through)
    return sim, warnings(moves, sim, ctx(today_sp=today, **context))


def codes(found) -> list[tuple[str, int | None]]:
    return [(w.code, w.move_id) for w in found]


# --- the engine stays pure ------------------------------------------------------------------


def test_the_engine_imports_no_database_or_espn_code():
    """Like the draft engine: importing it must not pull in SQLAlchemy, the models or ESPN."""
    script = (
        "import sys, app.team.plan; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] == 'sqlalchemy' "
        "or m.startswith(('app.db', 'app.espn', 'app.team.views', 'app.team.waivers', "
        "'app.team.store', 'app.config'))); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_engine_statuses_are_the_models():
    assert (plan.PLANNED, plan.DONE, plan.SKIPPED) == (PLAN_PLANNED, PLAN_DONE, PLAN_SKIPPED)


# --- moves ----------------------------------------------------------------------------------


def test_effective_pickup_and_alternatives():
    move = Move(1, 2, (A, B, C_), drop_player_id=X3)
    assert (effective_pickup(move), pickup_alternatives(move)) == (A, (B, C_))
    resolved = Move(1, 2, (A, B, C_), drop_player_id=X3, resolved_player_id=B)
    assert (effective_pickup(resolved), pickup_alternatives(resolved)) == (B, ())


# --- the simulation -------------------------------------------------------------------------


def test_a_single_move():
    sim, found = run([Move(1, 2, (A,), drop_player_id=X3)])

    assert list(sim.day(1).roster) == [X1, X2, X3]
    day2 = sim.day(2)
    assert list(day2.roster) == [X1, X2, A]
    assert day2.roster[A].status == "planned" and day2.roster[A].move_id == 1
    assert day2.roster[X1].status == "actual"
    assert [(d.player_id, d.move_id) for d in day2.planned_drops] == [(X3, 1)]
    # The next day keeps the roster and has no drops of its own; so does past the end.
    assert list(sim.day(5).roster) == [X1, X2, A] and sim.day(5).planned_drops == ()
    assert list(sim.day(40).roster) == [X1, X2, A]
    assert found == []


def test_two_moves_on_one_day_follow_their_sequence():
    first = Move(11, 2, (A,), sequence=0, drop_player_id=X3)
    second = Move(10, 2, (B,), sequence=1, drop_player_id=A)
    sim, found = run([second, first])
    assert list(sim.day(2).roster) == [X1, X2, B]
    assert found == []

    # Flip the order: dropping A before he's picked up fails, and both pickups land.
    sim, found = run([Move(11, 2, (A,), 1, drop_player_id=X3), Move(10, 2, (B,), 0, A)])
    assert list(sim.day(2).roster) == [X1, X2, B, A]
    assert codes(found) == [("drop_not_rostered", 10)]


def test_ties_on_sequence_fall_back_to_id():
    sim, _ = run([Move(2, 2, (B,), drop_player_id=A), Move(1, 2, (A,), drop_player_id=X3)])
    assert list(sim.day(2).roster) == [X1, X2, B]


def test_an_overdue_move_lands_today_and_says_so():
    sim, found = run([Move(1, 2, (A,), drop_player_id=X3)], today=5)

    assert A not in sim.day(4).roster  # before today nothing is applied
    assert A in sim.day(5).roster
    assert [d.player_id for d in sim.day(5).planned_drops] == [X3]
    overdue = [w for w in found if w.code == "overdue"]
    assert len(overdue) == 1
    assert overdue[0].severity == "info"
    assert overdue[0].sp == 5
    assert overdue[0].message == "Move 1 was planned for Wed 10/21 and hasn't been made yet"


def test_done_and_skipped_moves_are_not_applied():
    done = Move(1, 2, (A,), drop_player_id=X3, status=PLAN_DONE, resolved_player_id=A)
    skipped = Move(2, 3, (B,), drop_player_id=X2, status=PLAN_SKIPPED)
    sim, found = run([done, skipped])
    assert list(sim.day(5).roster) == [X1, X2, X3]
    assert found == []


def test_a_done_move_already_in_the_roster_is_not_counted_twice():
    """After the sync that marks a move done, RosterEntry already has the pickup."""
    done = Move(1, 2, (A, B), drop_player_id=X3, status=PLAN_DONE, resolved_player_id=A)
    later = Move(2, 4, (C_,), drop_from_move_id=1)
    sim = simulate((X1, X2, A), [done, later], 3, 6)
    found = warnings([done, later], sim, ctx(today_sp=3))

    assert list(sim.day(3).roster) == [X1, X2, A]
    assert list(sim.day(4).roster) == [X1, X2, C_]
    assert found == []
    tally = acquisition_tallies([done, later], ctx(today_sp=3, espn_used={1: 1}), [1])[0]
    assert (tally.used, tally.planned) == (1, 1)


# --- chains ---------------------------------------------------------------------------------


def test_the_chain_case():
    """Move 1 picks up A (backup B); move 2 drops "move 1's pickup". Unresolved, move 2 drops
    A (or B). Resolve move 1 to B and move 2 drops B."""
    move1 = Move(1, 2, (A, B), drop_player_id=X3)
    move2 = Move(2, 4, (C_,), drop_from_move_id=1)

    drop = resolve_drop(move2, {1: move1, 2: move2})
    assert (drop.player_id, drop.alternatives, drop.from_move_id) == (A, (B,), 1)
    assert drop.problem is None
    sim, found = run([move1, move2])
    assert list(sim.day(4).roster) == [X1, X2, C_]
    assert [(d.player_id, d.alternatives) for d in sim.day(4).planned_drops] == [(A, (B,))]
    assert found == []

    resolved = Move(1, 2, (A, B), drop_player_id=X3, resolved_player_id=B)
    drop = resolve_drop(move2, {1: resolved, 2: move2})
    assert (drop.player_id, drop.alternatives) == (B, ())
    sim, found = run([resolved, move2])
    assert list(sim.day(2).roster) == [X1, X2, B]
    assert list(sim.day(4).roster) == [X1, X2, C_]
    assert [d.player_id for d in sim.day(4).planned_drops] == [B]


def test_a_backup_that_is_a_candidate_elsewhere_overlaps():
    move1 = Move(1, 2, (A, B), drop_player_id=X3)
    move3 = Move(3, 5, (D, B), drop_player_id=X1)
    _, found = run([move1, move3])
    overlaps = [w for w in found if w.code == "backup_overlap"]
    assert [(w.move_id, w.detail["other_move_id"]) for w in overlaps] == [(1, 3), (3, 1)]
    assert overlaps[0].message == "Bam is a backup in move 1 and a backup in move 3"

    # Resolved to B, B is move 1's pickup and still a backup in move 3.
    resolved = Move(1, 2, (A, B), drop_player_id=X3, resolved_player_id=B)
    _, found = run([resolved, move3])
    overlaps = [w for w in found if w.code == "backup_overlap"]
    assert [w.message for w in overlaps] == ["Bam is a backup in move 3 and the pickup in move 1"]


def test_a_nested_chain_resolves_transitively():
    moves = [
        Move(1, 2, (A,), drop_player_id=X3),
        Move(2, 3, (B,), drop_from_move_id=1),
        Move(3, 4, (C_,), drop_from_move_id=2),
    ]
    by_id = {m.id: m for m in moves}
    assert resolve_drop(moves[2], by_id).player_id == B
    sim, found = run(moves)
    assert list(sim.day(4).roster) == [X1, X2, C_]
    assert found == []
    # The pickup that ends up in X3's slot is C, through A and B.
    assert slot_substitutions(sim.day(4)) == {C_: X3}


@pytest.mark.parametrize(
    "moves",
    [
        [Move(5, 2, (A,), drop_from_move_id=6), Move(6, 3, (B,), drop_from_move_id=5)],
        [Move(5, 2, (A,), drop_from_move_id=5)],
        [
            Move(5, 2, (A,), drop_from_move_id=7),
            Move(6, 3, (B,), drop_from_move_id=5),
            Move(7, 4, (C_,), drop_from_move_id=6),
        ],
    ],
)
def test_a_cycle_warns_and_terminates(moves):
    sim, found = run(moves)
    assert {code for code, _ in codes(found)} == {"chain_cycle"}
    assert sorted(move_id for _, move_id in codes(found)) == sorted(m.id for m in moves)
    # Nobody is dropped; every pickup still lands.
    assert set(sim.day(5).roster) == {X1, X2, X3} | {effective_pickup(m) for m in moves}


def test_a_loop_further_down_the_chain_does_not_spin_or_blame_this_move():
    moves = [
        Move(1, 2, (A,), drop_from_move_id=2),
        Move(2, 3, (B,), drop_from_move_id=1),
        Move(3, 4, (C_,), drop_from_move_id=2),
    ]
    by_id = {m.id: m for m in moves}
    assert resolve_drop(moves[2], by_id).player_id == B
    assert resolve_drop(moves[2], by_id).problem is None


@pytest.mark.parametrize(
    ("target", "reason", "message"),
    [
        (Move(1, 6, (A,), drop_player_id=X3), "later", "which comes later (Sun 10/25)"),
        (Move(1, 2, (A,), drop_player_id=X3, status=PLAN_SKIPPED), "skipped", "which is skipped"),
        (None, "missing", "which no longer exists"),
    ],
)
def test_a_bad_chain_warns_and_drops_nobody(target, reason, message):
    chained = Move(2, 4, (C_,), drop_from_move_id=1)
    moves = [chained] + ([target] if target else [])
    sim, found = run(moves)
    bad = [w for w in found if w.code == "bad_chain"]
    assert len(bad) == 1
    assert bad[0].detail["reason"] == reason
    assert message in bad[0].message
    assert C_ in sim.day(4).roster and X3 in sim.day(4).roster


# --- roster conflicts -----------------------------------------------------------------------


def test_dropping_a_player_already_dropped_still_applies_the_pickup():
    sim, found = run([Move(1, 2, (A,), drop_player_id=X3), Move(2, 3, (B,), drop_player_id=X3)])
    assert list(sim.day(3).roster) == [X1, X2, A, B]
    (warning,) = found
    assert (warning.code, warning.move_id, warning.sp) == ("drop_not_rostered", 2, 3)
    assert warning.detail["reason"] == "dropped_earlier"
    assert warning.message == (
        "Move 2 drops Jerami Grant on Thu 10/22, but he's already dropped by move 1; "
        "the pickup still happens"
    )


def test_dropping_someone_never_rostered():
    _, found = run([Move(1, 2, (A,), drop_player_id=D)])
    assert codes(found) == [("drop_not_rostered", 1)]
    assert found[0].detail["reason"] == "not_rostered"


def test_a_double_pickup():
    sim, found = run([Move(1, 2, (A,), drop_player_id=X3), Move(2, 3, (A,), drop_player_id=X2)])
    assert list(sim.day(3).roster) == [X1, A]
    assert codes(found) == [("double_pickup", 2)]
    assert found[0].message == "Moves 1 and 2 both pick up Alpha, with no drop of him in between"


def test_picking_up_someone_dropped_in_between_is_not_a_double_pickup():
    moves = [
        Move(1, 2, (A,), drop_player_id=X3),
        Move(2, 3, (B,), drop_player_id=A),
        Move(3, 6, (A,), drop_player_id=B),
    ]
    _, found = run(moves)
    assert "double_pickup" not in {w.code for w in found}


def test_picking_up_someone_already_on_my_roster():
    _, found = run([Move(1, 2, (X1,), drop_player_id=X3)])
    assert codes(found) == [("pickup_already_rostered", 1)]


# --- waivers --------------------------------------------------------------------------------


def test_dropping_then_re_adding_inside_the_waiver_period_warns_with_the_clear_date():
    """Jerami Grant, held since before the season, is dropped Wed 10/21 09:00 PT. Held more
    than 24h, so he's on waivers until drop + 24h (Thu 09:00) rolls to the next Pacific
    midnight: Fri 10/23 00:00 PDT = 07:00Z (the same rule as task 27's real data point)."""
    acquired = {X3: utc(2026, 10, 18, 20, 0)}
    drop = Move(1, 2, (A,), drop_player_id=X3)

    _, found = run([drop, Move(2, 3, (X3,), drop_player_id=A)], acquired_at=acquired)
    (warning,) = found
    assert (warning.code, warning.move_id, warning.sp) == ("on_waivers", 2, 3)
    assert warning.message == (
        "Jerami Grant is on waivers until Fri 10/23, but the move is Thu 10/22"
    )
    assert warning.detail["clears_at"] == "2026-10-23T07:00:00+00:00"
    assert warning.detail["source"] == "plan_drop"
    assert warning.detail["dropped_in_move"] == 1

    # Re-added the morning after he clears: no warning.
    _, found = run([drop, Move(2, 4, (X3,), drop_player_id=A)], acquired_at=acquired)
    assert found == []


def test_add_drop_re_add_of_a_planned_pickup():
    """Alpha arrives Wed 09:00 (planned), leaves Fri 09:00 (held 48h > 24h): on waivers
    until Sun 10/25 00:00 PDT. Re-added Sat: warns. Sun: fine."""
    pick = Move(1, 2, (A,), drop_player_id=X3)
    drop = Move(2, 4, (B,), drop_player_id=A)
    _, found = run([pick, drop, Move(3, 5, (A,), drop_player_id=B)])
    assert [w.message for w in found] == [
        "Alpha is on waivers until Sun 10/25, but the move is Sat 10/24"
    ]
    _, found = run([pick, drop, Move(3, 6, (A,), drop_player_id=B)])
    assert found == []


def test_a_player_held_under_the_window_goes_straight_back_to_free_agency():
    moves = [
        Move(1, 2, (A,), sequence=0, drop_player_id=X3),
        Move(2, 2, (B,), sequence=1, drop_player_id=A),
        Move(3, 2, (A,), sequence=2, drop_player_id=B),
    ]
    _, found = run(moves)
    assert "on_waivers" not in {w.code for w in found}


def test_a_player_espn_has_on_waivers_with_a_known_clear():
    move = Move(1, 2, (A,), drop_player_id=X3)
    _, found = run([move], on_waivers={A: utc(2026, 10, 22, 7, 0)})
    (warning,) = found
    assert warning.message == "Alpha is on waivers until Thu 10/22, but the move is Wed 10/21"
    assert warning.detail["source"] == "espn"

    _, found = run([Move(1, 3, (A,), drop_player_id=X3)], on_waivers={A: utc(2026, 10, 22, 7)})
    assert found == []


def test_a_player_espn_has_on_waivers_with_no_known_clear():
    _, found = run([Move(1, 2, (A,), drop_player_id=X3)], on_waivers={A: None})
    (warning,) = found
    assert warning.code == "on_waivers"
    assert warning.message == "Alpha is on waivers on Wed 10/21 and his clear time is unknown"


def test_the_move_time_setting_moves_the_answer():
    """At 23:30 PT on Thu 10/22, a 07:00Z (00:00 PT) Thursday clear has long passed;
    at 09:00 Wed it hasn't."""
    move = Move(1, 3, (A,), drop_player_id=X3)
    _, found = run([move], on_waivers={A: utc(2026, 10, 23, 7, 0)}, move_time=time(23, 30))
    assert [w.code for w in found] == ["on_waivers"]
    _, found = run([move], on_waivers={A: utc(2026, 10, 23, 7, 0)}, move_time=time(0, 0))
    assert [w.code for w in found] == ["on_waivers"]
    _, found = run([move], on_waivers={A: utc(2026, 10, 22, 7, 0)}, move_time=time(0, 0))
    assert found == []


# --- acquisitions ---------------------------------------------------------------------------


def _seven_pickups(status=PLAN_PLANNED):
    return [
        Move(i, 1 + (i % 6), (500 + i,), drop_player_id=900 + i, status=status) for i in range(7)
    ]


def test_espn_count_plus_planned_over_the_limit_warns():
    sim = simulate(ROSTER, _seven_pickups(), 1, 10)
    found = warnings(_seven_pickups(), sim, ctx(acquisition_limit=7, espn_used={1: 1}))
    (acq,) = [w for w in found if w.code == "acq_limit"]
    assert acq.message == "Matchup 1: 8 of 7 acquisitions (1 used + 7 planned)"
    assert acq.matchup_id == 1 and acq.move_id is None
    assert acq.detail == {"used": 1, "used_source": "espn", "planned": 7, "limit": 7}


def test_an_override_replaces_espn_count():
    moves = _seven_pickups()
    tally = acquisition_tallies(moves, ctx(acquisition_limit=7, espn_used={1: 1}), [1])[0]
    assert (tally.used, tally.used_source, tally.over) == (1, "espn", True)
    override = ctx(acquisition_limit=7, espn_used={1: 1}, used_overrides={1: 0})
    tally = acquisition_tallies(moves, override, [1])[0]
    assert (tally.used, tally.used_source, tally.planned, tally.over) == (0, "override", 7, False)
    sim = simulate(ROSTER, moves, 1, 10)
    assert "acq_limit" not in {w.code for w in warnings(moves, sim, override)}


def test_done_moves_are_not_counted_twice():
    moves = _seven_pickups()
    moves[0] = Move(0, 1, (500,), drop_player_id=900, status=PLAN_DONE)
    tally = acquisition_tallies(moves, ctx(acquisition_limit=7, espn_used={1: 1}), [1])[0]
    assert (tally.used, tally.planned, tally.over) == (1, 6, False)


def test_planned_pickups_count_in_the_matchup_they_land_in():
    moves = [Move(1, 2, (A,), drop_player_id=X3), Move(2, 8, (B,), drop_player_id=X2)]
    tallies = acquisition_tallies(moves, ctx(acquisition_limit=7), [1, 2])
    assert [(t.matchup_id, t.planned) for t in tallies] == [(1, 1), (2, 1)]
    # Overdue: sp 2 lands today (sp 8, matchup 2).
    tallies = acquisition_tallies(moves, ctx(today_sp=8, acquisition_limit=7), [2])
    assert tallies[0].planned == 2
    assert plan.plan_matchup_ids(moves, SEASON, 1) == [1, 2]


# --- infos ----------------------------------------------------------------------------------


def test_no_game_on_the_move_day_is_an_info():
    moves = [Move(1, 2, (A,), drop_player_id=X3), Move(2, 3, (B,), drop_player_id=X2)]
    _, found = run(moves, games={(2, A)})
    (info,) = found
    assert (info.code, info.severity, info.move_id) == ("no_game", "info", 2)
    assert info.message == "Bam has no game on Thu 10/22 (move 2)"
    # No schedule known: no infos at all.
    _, found = run(moves)
    assert found == []


def test_a_done_move_that_dropped_someone_else_is_an_info():
    move = Move(
        1,
        2,
        (A,),
        drop_player_id=X3,
        status=PLAN_DONE,
        resolved_player_id=A,
        drop_mismatch=True,
        detected_drop_player_id=X2,
    )
    _, found = run([move])
    (info,) = found
    assert (info.code, info.severity) == ("different_drop", "info")
    assert info.message == "Move 1 was made on ESPN, but you dropped Xander Two, not Jerami Grant"


# --- the Plan lineup ------------------------------------------------------------------------

ACTUAL = {X1: PG, X2: SG, X3: BE}
ELIGIBLE = {A: [PG, SG, UT, BE, IR], B: [C, UT, BE, IR], C_: [PG, UT, BE, IR]}


def test_the_pickup_takes_the_dropped_players_slot():
    sim = simulate(ROSTER, [Move(1, 2, (A,), drop_player_id=X1)], 1, 5)
    lineup = plan_lineup(sim.day(3), ACTUAL, {}, ELIGIBLE)
    assert lineup.placement == {X2: SG, X3: BE, A: PG}
    assert (lineup.source, lineup.carried_from_sp) == ("from_actual", None)
    # Before the move lands, the plan lineup is the actual one.
    assert plan_lineup(sim.day(1), ACTUAL, {}, ELIGIBLE).placement == ACTUAL


def test_a_dropped_bench_player_means_the_pickup_sits_on_the_bench():
    sim = simulate(ROSTER, [Move(1, 2, (A,), drop_player_id=X3)], 1, 5)
    assert plan_lineup(sim.day(2), ACTUAL, {}, ELIGIBLE).placement == {X1: PG, X2: SG, A: BE}


def test_an_ineligible_pickup_goes_to_the_bench_not_the_slot():
    sim = simulate(ROSTER, [Move(1, 2, (B,), drop_player_id=X1)], 1, 5)
    assert plan_lineup(sim.day(2), ACTUAL, {}, ELIGIBLE).placement == {X2: SG, X3: BE, B: BE}


def test_a_chained_pickup_inherits_the_original_slot():
    moves = [Move(1, 2, (A,), drop_player_id=X1), Move(2, 4, (C_,), drop_from_move_id=1)]
    sim = simulate(ROSTER, moves, 1, 5)
    assert slot_substitutions(sim.day(4)) == {C_: X1}
    assert plan_lineup(sim.day(4), ACTUAL, {}, ELIGIBLE).placement == {X2: SG, X3: BE, C_: PG}


def test_plan_edits_carry_forward_and_later_pickups_still_take_slots():
    moves = [Move(1, 2, (A,), drop_player_id=X1), Move(2, 5, (C_,), drop_player_id=A)]
    sim = simulate(ROSTER, moves, 1, 6)
    edited = {3: {X2: BE, X3: SG, A: PG}}

    on_day = plan_lineup(sim.day(3), ACTUAL, edited, ELIGIBLE)
    assert (on_day.source, on_day.placement) == ("plan_edit", {X2: BE, X3: SG, A: PG})
    carried = plan_lineup(sim.day(4), ACTUAL, edited, ELIGIBLE)
    assert (carried.source, carried.carried_from_sp) == ("plan_carried", 3)
    assert carried.placement == {X2: BE, X3: SG, A: PG}
    # Move 2 lands after the edited day: C takes A's planned slot, not X1's actual one.
    assert plan_lineup(sim.day(5), ACTUAL, edited, ELIGIBLE).placement == {X2: BE, X3: SG, C_: PG}


def test_a_deleted_move_reconciles_out_of_a_plan_edit():
    edited = {3: {X2: BE, X3: SG, A: PG}}
    sim = simulate(ROSTER, [], 1, 6)
    # A is gone; X1 (no longer dropped) is back and, absent from the edit, on the bench.
    assert plan_lineup(sim.day(4), ACTUAL, edited, ELIGIBLE).placement == {X1: BE, X2: BE, X3: SG}
