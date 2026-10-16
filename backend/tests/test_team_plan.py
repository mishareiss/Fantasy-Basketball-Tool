"""The waiver plan's pure engine: moves, chains, the simulation, the Plan lineup and every
warning. No database. Day 1 of 2027 is Tue 10/20/2026, so sp 2 is Wed 10/21, sp 3 Thu 10/22.
"""

import re
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
    move_label,
    move_labels,
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
    assert overdue[0].message == (
        "The Wed 10/21 move (Jerami Grant → Alpha) hasn't been made yet, so the plan has it land "
        "today"
    )


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
    assert overlaps[0].message == (
        "Bam is a backup in the Wed 10/21 move (Jerami Grant → Alpha) and a backup in the "
        "Sat 10/24 move (Xavier One → player 104)"
    )

    # Resolved to B, B is move 1's pickup and still a backup in move 3.
    resolved = Move(1, 2, (A, B), drop_player_id=X3, resolved_player_id=B)
    _, found = run([resolved, move3])
    overlaps = [w for w in found if w.code == "backup_overlap"]
    assert [w.message for w in overlaps] == [
        "Bam is a backup in the Sat 10/24 move (Xavier One → player 104) and the pickup in the "
        "Wed 10/21 move (Jerami Grant → Bam)"
    ]


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
        (
            Move(1, 6, (A,), drop_player_id=X3),
            "later",
            "whoever the Sun 10/25 move (Jerami Grant → Alpha) picks up, which comes later;",
        ),
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
        "The Thu 10/22 move (Jerami Grant → Bam) can't drop Jerami Grant: he's already dropped "
        "by the Wed 10/21 move (Jerami Grant → Alpha); the pickup still happens"
    )


def test_dropping_someone_never_rostered():
    _, found = run([Move(1, 2, (A,), drop_player_id=D)])
    assert codes(found) == [("drop_not_rostered", 1)]
    assert found[0].detail["reason"] == "not_rostered"


def test_a_double_pickup():
    sim, found = run([Move(1, 2, (A,), drop_player_id=X3), Move(2, 3, (A,), drop_player_id=X2)])
    assert list(sim.day(3).roster) == [X1, A]
    assert codes(found) == [("double_pickup", 2)]
    assert found[0].message == (
        "The Wed 10/21 move (Jerami Grant → Alpha) and the Thu 10/22 move (Xander Two → Alpha) "
        "both pick up Alpha, with no drop of him in between"
    )


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


def test_no_schedule_means_no_stream_infos():
    moves = [Move(1, 2, (A,), drop_player_id=X3), Move(2, 3, (B,), drop_player_id=X2)]
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
    assert info.message == (
        "The Wed 10/21 move (Jerami Grant → Alpha) was made on ESPN, but you dropped Xander Two, "
        "not Jerami Grant"
    )


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


# --- how messages name a move ---------------------------------------------------------------


def _name(pid):
    return "nobody" if pid is None else NAMES.get(pid, f"player {pid}")


def _day(sp):
    return plan.day_label(plan.period_date(SEASON, sp))


def label(move, moves):
    return move_label(move, {m.id: m for m in moves}, _name, _day)


def test_a_named_drop_is_labelled_by_its_day_and_players():
    move = Move(7, 2, (A, B), drop_player_id=X3)
    assert label(move, [move]) == "the Wed 10/21 move (Jerami Grant → Alpha)"


def test_a_chained_drop_is_labelled_by_whoever_it_resolves_to():
    first = Move(1, 2, (A, B), drop_player_id=X3)
    chained = Move(2, 4, (C_,), drop_from_move_id=1)
    # Unresolved: the referenced move's primary.
    assert label(chained, [first, chained]) == "the Fri 10/23 move (Alpha → Cole)"
    resolved = Move(1, 2, (A, B), drop_player_id=X3, status=PLAN_DONE, resolved_player_id=B)
    assert label(chained, [resolved, chained]) == "the Fri 10/23 move (Bam → Cole)"
    # The move's own pickup follows its resolution too.
    assert label(resolved, [resolved]) == "the Wed 10/21 move (Jerami Grant → Bam)"


def test_a_broken_chain_is_an_unresolved_drop():
    missing = Move(2, 4, (C_,), drop_from_move_id=99)
    assert label(missing, [missing]) == "the Fri 10/23 move (an unresolved drop → Cole)"
    skipped = Move(1, 2, (A,), drop_player_id=X3, status=PLAN_SKIPPED)
    chained = Move(2, 4, (C_,), drop_from_move_id=1)
    assert label(chained, [skipped, chained]) == "the Fri 10/23 move (an unresolved drop → Cole)"


def test_identical_labels_get_a_suffix_in_plan_order():
    moves = [
        Move(9, 2, (A,), drop_player_id=X3, sequence=1),
        Move(4, 2, (A,), drop_player_id=X3, sequence=0),
        Move(5, 3, (A,), drop_player_id=X3),
    ]
    labels = move_labels(moves, {m.id: m for m in moves}, _name, _day)
    assert labels == {
        4: "the Wed 10/21 move (Jerami Grant → Alpha)",
        9: "the Wed 10/21 move (Jerami Grant → Alpha) #2",
        5: "the Thu 10/22 move (Jerami Grant → Alpha)",
    }


def test_no_warning_names_a_move_by_number():
    """One plan that fires every warning that mentions a move: each message names the move by
    its label, and none says "move 3" or "Moves 1 and 2"."""
    moves = [
        Move(1, 2, (A, B), drop_player_id=X3),  # overdue at today=3; B overlaps with move 5
        Move(2, 3, (C_,), drop_player_id=X3),  # X3 already dropped by move 1
        Move(3, 3, (X1,), drop_player_id=D),  # D never rostered; X1 already rostered
        Move(4, 4, (A,), drop_player_id=X2),  # A picked up twice
        Move(5, 4, (D, B), drop_from_move_id=6),  # chain to a later move
        Move(6, 6, (C_,), drop_player_id=X1),
        Move(7, 5, (D,), drop_from_move_id=8),  # a cycle
        Move(8, 5, (B,), drop_from_move_id=7),
        Move(10, 5, (D,), drop_from_move_id=99),  # a chain to a deleted move
        Move(
            11,
            3,
            (C_,),
            drop_player_id=X2,
            status=PLAN_DONE,
            resolved_player_id=C_,
            drop_mismatch=True,
            detected_drop_player_id=X1,
        ),
    ]
    # Jerami Grant (X3) plays the day move 1 drops him before tip-off; Alpha plays Fri.
    _, found = run(moves, today=3, games={(3, X3), (4, A)})
    mentioned = {
        "drop_not_rostered",
        "pickup_already_rostered",
        "double_pickup",
        "bad_chain",
        "chain_cycle",
        "backup_overlap",
        "stream_no_games",
        "stream_info",
        "drop_game_lost",
        "overdue",
        "different_drop",
    }
    assert mentioned <= {w.code for w in found}
    chained = ("bad_chain", "drop_not_rostered")
    reasons = {w.detail.get("reason") for w in found if w.code in chained}
    assert {"later", "missing", "dropped_earlier", "not_rostered"} <= reasons
    for w in found:
        assert not re.search(r"\b[Mm]oves? \d", w.message), w.message
        if w.code in mentioned:
            assert re.search(r"[Tt]he \w{3} \d+/\d+ move \(", w.message), w.message
    # The ids are still there for the UI, outside the prose.
    assert {w.move_id for w in found if w.code in mentioned} >= {1, 2, 3, 4, 5, 7, 11}
    deleted = next(w for w in found if w.code == "bad_chain" and w.move_id == 10)
    assert "whoever a deleted move picks up" in deleted.message


# --- move timing: made on D, landing on D (before games) or D+1 (after tip-off) -------------

AFTER = plan.AFTER_TIPOFF
BEFORE = plan.BEFORE_GAMES


def test_after_tipoff_keeps_the_dropped_player_on_the_day_and_adds_the_pickup_next_day():
    move = Move(1, 2, (A, B), drop_player_id=X1, timing=AFTER)
    sim = simulate(ROSTER, [move], 1, 5)

    wed, thu = sim.day(2), sim.day(3)
    assert set(wed.roster) == {X1, X2, X3}
    assert [(d.player_id, d.move_id) for d in wed.leaving] == [(X1, 1)]
    assert [(e.player_id, e.move_id, e.alternatives) for e in wed.joining] == [(A, 1, (B,))]
    assert wed.planned_drops == ()
    assert set(thu.roster) == {X2, X3, A}
    # The drop was announced the night before, not again on the day it lands.
    assert (thu.planned_drops, thu.leaving, thu.joining) == ((), (), ())
    (event,) = sim.applied
    assert (event.sp, event.made, event.timing) == (3, 2, AFTER)
    assert plan.made_period(move, 1) == 2 and plan.landing_period(move, 1) == 3


def test_before_games_is_the_old_behaviour():
    move = Move(1, 2, (A,), drop_player_id=X1, timing=BEFORE)
    sim = simulate(ROSTER, [move], 1, 5)
    assert set(sim.day(2).roster) == {X2, X3, A}
    assert [d.player_id for d in sim.day(2).planned_drops] == [X1]
    assert sim.day(2).leaving == () and sim.day(2).joining == ()
    assert plan.landing_period(move, 1) == 2


def test_an_overdue_after_tipoff_move_is_made_today_and_lands_tomorrow():
    move = Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)
    sim, found = run([move], today=4)
    assert X1 in sim.day(4).roster and A in sim.day(5).roster
    (overdue,) = [w for w in found if w.code == "overdue"]
    assert overdue.sp == 4
    assert overdue.message.endswith("so the plan has it made tonight")


def test_a_chain_with_mixed_timings():
    """Move 1 (Wed, after tip-off) brings Alpha in Thu. Move 2 (Thu, after tip-off) drops
    him for Bam from Fri; move 3 (Fri, before games) drops Bam for Cole that morning."""
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 3, (B,), drop_from_move_id=1, timing=AFTER),
        Move(3, 4, (C_,), drop_from_move_id=2, timing=BEFORE),
    ]
    sim = simulate(ROSTER, moves, 1, 6)
    assert {X1}.issubset(sim.day(2).roster)
    assert A in sim.day(3).roster and X1 not in sim.day(3).roster
    assert [d.player_id for d in sim.day(3).leaving] == [A]
    # Fri: Bam lands and leaves before Fri's games; Cole holds X1's slot.
    assert C_ in sim.day(4).roster and B not in sim.day(4).roster and A not in sim.day(4).roster
    assert sim.conflicts == ()
    assert slot_substitutions(sim.day(4)) == {C_: X1}


def test_a_before_games_move_cant_drop_a_pickup_who_only_joins_that_night():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 2, (B,), drop_from_move_id=1, sequence=1, timing=BEFORE),
    ]
    sim = simulate(ROSTER, moves, 1, 5)
    assert [(c.code, c.move_id) for c in sim.conflicts] == [("drop_not_rostered", 2)]


def test_the_lineup_substitution_lands_the_day_after():
    sim = simulate(ROSTER, [Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)], 1, 5)
    assert plan_lineup(sim.day(2), ACTUAL, {}, ELIGIBLE).placement == ACTUAL
    assert slot_substitutions(sim.day(2)) == {}
    assert plan_lineup(sim.day(3), ACTUAL, {}, ELIGIBLE).placement == {X2: SG, X3: BE, A: PG}


# --- the stream window ----------------------------------------------------------------------


def test_a_stream_window_ends_the_day_before_a_chained_drop_lands():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),  # Alpha from Thu
        Move(2, 5, (B,), drop_from_move_id=1, timing=AFTER),  # made Sat night, lands Sun
    ]
    windows = plan.stream_windows(moves, 1, 6)
    assert windows[1] == plan.StreamWindow(1, A, 3, 5, False, 2)
    assert (windows[2].first_sp, windows[2].last_sp, windows[2].open_ended) == (6, 6, True)


def test_a_stream_window_ends_at_a_named_drop_of_the_pickup():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 5, (B,), drop_player_id=A, timing=BEFORE),  # lands Sat itself
    ]
    window = plan.stream_windows(moves, 1, 6)[1]
    assert (window.first_sp, window.last_sp, window.ended_by) == (3, 4, 2)
    assert list(window.days) == [3, 4]


def test_a_chain_through_another_move_that_resolves_to_him_ends_the_window_too():
    """Two moves pick up Alpha (a double pickup the plan warns about); a later move drops
    "move 2's pickup", which is Alpha. The simulation drops him then, so move 1's window ends
    there as well, not at the end of the plan."""
    moves = [
        Move(1, 1, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 3, (A,), drop_player_id=X2, timing=BEFORE),
        Move(3, 5, (B,), drop_from_move_id=2, timing=BEFORE),
    ]
    windows = plan.stream_windows(moves, 1, 6)
    assert (windows[1].last_sp, windows[1].ended_by) == (4, 3)
    sim = simulate(ROSTER, moves, 1, 6)
    assert A in sim.day(4).roster and A not in sim.day(5).roster


def test_an_open_ended_window_runs_to_the_end_of_the_plan():
    moves = [Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)]
    assert plan.plan_horizon_end(moves, SEASON, 1) == 6  # Matchup 1 ends Sun 10/25
    window = plan.stream_windows(moves, 1, 6)[1]
    assert (window.first_sp, window.last_sp, window.open_ended, window.ended_by) == (
        3,
        6,
        True,
        None,
    )


def test_skipped_moves_neither_stream_nor_end_a_window():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 4, (B,), drop_from_move_id=1, status=PLAN_SKIPPED),
    ]
    windows = plan.stream_windows(moves, 1, 6)
    assert set(windows) == {1} and windows[1].open_ended


def test_stream_info_says_when_he_plays_and_what_drops_him():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 5, (B,), drop_player_id=A, timing=AFTER),
    ]
    _, found = run(moves, games={(3, A), (6, B)})
    info = next(w for w in found if w.code == "stream_info" and w.move_id == 1)
    assert info.severity == "info" and info.sp == 2
    assert info.message == (
        "The Wed 10/21 move (Xavier One → Alpha): Alpha plays Thu 10/22 (1 game) "
        "before the Sat 10/24 move (Alpha → Bam) drops him"
    )
    assert info.detail == {
        "player_id": A,
        "first_sp": 3,
        "last_sp": 5,
        "games": 1,
        "ended_by": 2,
    }
    last = next(w for w in found if w.move_id == 2 and w.code == "stream_info")
    assert last.message == (
        "The Sat 10/24 move (Alpha → Bam): Bam plays Sun 10/25 (1 game) "
        "through Sun 10/25, the end of the plan"
    )


def test_stream_no_games_warns():
    moves = [Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)]
    _, found = run(moves, games={(2, A)})  # his only game is the night he's added
    (warning,) = found
    assert (warning.code, warning.severity) == ("stream_no_games", "warning")
    assert warning.message == (
        "The Wed 10/21 move (Xavier One → Alpha) picks up Alpha, who plays no games "
        "through Sun 10/25, the end of the plan"
    )


def test_a_pickup_dropped_the_day_he_lands_never_plays():
    moves = [
        Move(1, 2, (A,), drop_player_id=X1, timing=AFTER),
        Move(2, 3, (B,), drop_from_move_id=1, timing=BEFORE),
    ]
    _, found = run(moves, games={(3, A), (3, B)})
    warning = next(w for w in found if w.code == "stream_no_games")
    assert warning.move_id == 1
    assert warning.message.endswith(
        "who is dropped by the Thu 10/22 move (Alpha → Bam) before he plays a game"
    )


def test_drop_game_lost_is_only_for_before_games():
    before = [Move(1, 2, (A,), drop_player_id=X1, timing=BEFORE)]
    _, found = run(before, games={(2, X1), (2, A)})
    lost = next(w for w in found if w.code == "drop_game_lost")
    assert (lost.severity, lost.sp, lost.detail) == ("info", 2, {"player_id": X1})
    assert lost.message == (
        "The Wed 10/21 move (Xavier One → Alpha) is made before tip-off, so you give up "
        "Xavier One's Wed 10/21 game"
    )
    after = [Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)]
    _, found = run(after, games={(2, X1), (3, A)})
    assert "drop_game_lost" not in {w.code for w in found}


# --- waiver math from the first tip-off -----------------------------------------------------


def test_an_after_tipoff_move_happens_at_the_days_first_tip():
    """Alpha clears 16:30 PT Wed. A move at Wed's first tip (16:00 PT) is too early; one at a
    17:00 PT tip is not; a day without games falls back to 16:00 PT; 09:00 is too early."""
    clears = {A: utc(2026, 10, 21, 23, 30)}
    move = Move(1, 2, (A,), drop_player_id=X1, timing=AFTER)

    _, found = run([move], on_waivers=clears, first_tip={2: utc(2026, 10, 21, 23, 0)})
    assert [w.code for w in found] == ["on_waivers"]
    assert found[0].sp == 2 and found[0].message.endswith("but the move is Wed 10/21")
    _, found = run([move], on_waivers=clears, first_tip={2: utc(2026, 10, 22, 0, 0)})
    assert found == []
    _, found = run([move], on_waivers=clears)
    assert [w.code for w in found] == ["on_waivers"]
    morning = Move(1, 2, (A,), drop_player_id=X1, timing=BEFORE)
    _, found = run([morning], on_waivers=clears, first_tip={2: utc(2026, 10, 22, 0, 0)})
    assert [w.code for w in found] == ["on_waivers"]


def test_the_waiver_clock_starts_at_tip_off_across_the_dst_change():
    """Jerami Grant is dropped Sat 10/31 (sp 12) after the 7:00 PM PDT tip (02:00Z Sun) with a
    14h waiver period: drop + 14h is 08:00 PST Sun 11/1, the day clocks fall back, so he clears
    at the next Pacific midnight, Mon 11/2 00:00 PST = 08:00Z. Re-adding him Sun morning warns.
    Dropped Sat 09:00 PDT instead, he'd clear Sun 00:00 PDT = 07:00Z, before that re-add."""
    acquired = {X3: utc(2026, 10, 1)}
    tip = {12: utc(2026, 11, 1, 2, 0)}
    readd = Move(2, 13, (X3,), drop_player_id=A, timing=BEFORE)

    moves = [Move(1, 12, (A,), drop_player_id=X3, timing=AFTER), readd]
    _, found = run(moves, through=14, acquired_at=acquired, first_tip=tip, waiver_hours=14)
    (warning,) = [w for w in found if w.code == "on_waivers"]
    assert warning.detail["clears_at"] == "2026-11-02T08:00:00+00:00"
    assert warning.message == "Jerami Grant is on waivers until Mon 11/2, but the move is Sun 11/1"

    moves = [Move(1, 12, (A,), drop_player_id=X3, timing=BEFORE), readd]
    _, found = run(moves, through=14, acquired_at=acquired, first_tip=tip, waiver_hours=14)
    assert "on_waivers" not in {w.code for w in found}


# --- acquisitions are counted on the day the move is made -----------------------------------


def test_a_sunday_after_tipoff_move_spends_the_ending_matchups_budget():
    """Sun 10/25 (sp 6) is Matchup 1's last day. The pickup plays from Mon (Matchup 2), but
    the acquisition is Sunday's."""
    move = Move(1, 6, (A,), drop_player_id=X1, timing=AFTER)
    tallies = acquisition_tallies([move], ctx(acquisition_limit=7), [1, 2])
    assert [(t.matchup_id, t.planned) for t in tallies] == [(1, 1), (2, 0)]
    assert plan.plan_matchup_ids([move], SEASON, 1) == [1, 2]
    sim = simulate(ROSTER, [move], 1, 8)
    assert X1 in sim.day(6).roster and A in sim.day(7).roster
