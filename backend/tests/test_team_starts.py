"""The starts tracker's pure aggregation: filled, possible, empty and left on the bench."""

import subprocess
import sys

from app.team.starts import day_starts, matchup_starts
from tests.test_team_autofill import BE, COUNTS, IR, PF, PG, SF, SG, UT, C, _p

POSITIONS = ["PG", "SG", "SF", "PF", "C", "PF/C", "PG/SG"]


def test_the_aggregation_imports_no_database_or_espn_code():
    script = (
        "import sys, app.team.starts; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('sqlalchemy', 'espn_api') "
        "or m.startswith(('app.db', 'app.espn', 'app.team.views', 'app.config'))); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_fourteen_playing_fill_seven_and_bench_seven():
    players = [_p(i, POSITIONS[i % len(POSITIONS)], 50.0 - i) for i in range(14)]
    day = day_starts(1, players, COUNTS)
    assert (day.slots, day.possible, day.empty) == (7, 7, 0)
    assert len(day.benched_with_games) == 7
    # Nobody is in a starting slot as set (all on the bench): every possible start is lost.
    assert (day.filled, day.left_on_bench) == (0, 7)


def test_four_playing_leave_three_open():
    players = [_p(1, "PG", 30), _p(2, "SF", 25), _p(3, "C", 20), _p(4, "PF", 15)]
    players += [_p(10 + i, "SG", 40, game=False) for i in range(6)]
    day = day_starts(1, players, COUNTS)
    assert (day.possible, day.empty, day.benched_with_games) == (4, 3, ())


def test_positions_cap_what_is_possible():
    # Eight guards and one center with games: PG, SG, UT, UT and C. SF and PF stay empty.
    players = [_p(i, "PG/SG" if i % 2 else "PG", 40 - i) for i in range(8)] + [_p(20, "C", 10)]
    day = day_starts(1, players, COUNTS)
    assert (day.possible, day.empty) == (5, 2)
    assert len(day.benched_with_games) == 4


def test_out_and_suspended_do_not_play_and_ir_never_counts():
    players = [
        _p(1, "PG", 50, injury="OUT"),
        _p(2, "SG", 50, injury="SUSPENSION"),
        _p(3, "SF", 50, slot=IR),  # healthy, has a game, but on IR
        _p(4, "PF", 20, injury="DAY_TO_DAY"),  # still plays
        _p(5, "C", 50, slot=C, injury="OUT"),  # starting as set, but doesn't play
    ]
    day = day_starts(1, players, COUNTS)
    assert (day.possible, day.filled, day.empty) == (1, 0, 6)
    assert day.benched_with_games == ()
    assert day.projected_points == 20.0


def test_left_on_bench_counts_starts_the_set_lineup_loses():
    players = [
        _p(1, "PG", 40, slot=PG),
        _p(2, "SG", 35, slot=SG),
        _p(3, "SF", 30, slot=BE),  # plays, benched with SF open
        _p(4, "PF", 25, slot=PF),
        _p(5, "C", 20, slot=C, game=False),  # starting, no game
        _p(6, "C", 15, slot=BE),  # plays, benched behind him
    ]
    day = day_starts(1, players, COUNTS)
    assert (day.filled, day.possible, day.left_on_bench, day.empty) == (3, 5, 2, 2)
    assert day.projected_points == 145.0
    assert day.projected_points_as_set == 100.0
    assert day.benched_with_games == ()


def test_an_overfilled_stored_slot_counts_its_rows_only():
    players = [_p(1, "PG", 40, slot=PG), _p(2, "PG", 30, slot=PG), _p(3, "PG", 20, slot=UT)]
    day = day_starts(1, players, COUNTS)
    assert day.filled == 2 and day.projected_points_as_set == 60.0
    assert day.left_on_bench == 1


def test_benched_with_games_are_best_first():
    players = [_p(i, "PG", float(i)) for i in range(1, 6)]
    day = day_starts(1, players, COUNTS)
    # PG, UT, UT take the three best; 2 and 1 sit.
    assert day.possible == 3 and day.benched_with_games == (2, 1)


def test_matchup_totals_sum_the_days():
    busy = [_p(i, POSITIONS[i % len(POSITIONS)], 10.0, slot=BE) for i in range(14)]
    starters = {0: PG, 1: SG, 2: SF, 3: PF, 4: C}
    busy = [
        _p(p.id, POSITIONS[p.id % len(POSITIONS)], 10.0, slot=starters.get(p.id, BE)) for p in busy
    ]
    quiet = [_p(1, "PG", 30, slot=PG), _p(2, "SG", 20, slot=BE), _p(3, "SG", 10, game=False)]
    days = [day_starts(1, busy, COUNTS, past=True), day_starts(2, quiet, COUNTS)]
    assert days[0].past and not days[1].past
    totals = matchup_starts(days)
    assert (totals.days, totals.max) == (2, 14)
    assert (totals.filled, totals.possible) == (5 + 1, 7 + 2)
    assert (totals.empty, totals.left_on_bench) == (0 + 5, 2 + 1)
    assert totals.projected_points == 70.0 + 50.0
    assert totals.projected_points_as_set == 50.0 + 30.0


def test_an_empty_roster_has_every_start_open():
    day = day_starts(1, [], COUNTS)
    assert (day.filled, day.possible, day.empty, day.left_on_bench) == (0, 0, 7, 0)
