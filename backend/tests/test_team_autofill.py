"""Auto-fill's pure optimizer: who starts, where, and who sits."""

import subprocess
import sys

from app.team.autofill import AutoFillPlayer, best_lineup, starting_slots
from app.team.grid import LineupCandidate, validate_lineup

PG, SG, SF, PF, C, G, F, UT, BE, IR = 0, 1, 2, 3, 4, 5, 6, 11, 12, 13
COUNTS = {PG: 1, SG: 1, SF: 1, PF: 1, C: 1, UT: 2, BE: 10, IR: 3}

ELIGIBLE = {
    "PG": [PG, G, UT, BE, IR],
    "SG": [SG, G, UT, BE, IR],
    "SF": [SF, F, UT, BE, IR],
    "PF": [PF, F, UT, BE, IR],
    "C": [C, UT, BE, IR],
    "PF/C": [PF, C, F, UT, BE, IR],
    "PG/SG": [PG, SG, G, UT, BE, IR],
}


def _p(pid, pos, value, *, game=True, slot=BE, injury=None):
    return AutoFillPlayer(
        id=pid,
        eligible_slots=ELIGIBLE[pos],
        slot_id=slot,
        has_game=game,
        value=value,
        injury_status=injury,
    )


def _starters(lineup):
    return {pid: slot for pid, slot in lineup.items() if slot not in (BE, IR)}


def _assert_legal(players, lineup):
    roster = {
        p.id: LineupCandidate(p.id, str(p.id), p.eligible_slots, p.injury_status) for p in players
    }
    errors, _ = validate_lineup(list(lineup.items()), roster, COUNTS)
    assert errors == []


def test_the_optimizer_imports_no_database_or_espn_code():
    script = (
        "import sys, app.team.autofill; "
        "bad = sorted(m for m in sys.modules if m.split('.')[0] in ('sqlalchemy', 'espn_api') "
        "or m.startswith(('app.db', 'app.espn', 'app.team.views', 'app.team.store', "
        "'app.config'))); "
        "print(bad); sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_starting_slots_skip_bench_and_ir():
    assert starting_slots(COUNTS) == [PG, SG, SF, PF, C, UT, UT]


def test_more_players_than_slots_starts_the_best_and_benches_the_rest():
    players = [
        _p(1, "PG", 50),
        _p(2, "SG", 45),
        _p(3, "SF", 40),
        _p(4, "PF", 35),
        _p(5, "C", 30),
        _p(6, "PG", 25),
        _p(7, "SG", 20),
        _p(8, "SF", 10),
        _p(9, "C", 5),
    ]
    lineup = best_lineup(players, COUNTS)

    assert _starters(lineup) == {1: PG, 2: SG, 3: SF, 4: PF, 5: C, 6: UT, 7: UT}
    assert lineup[8] == lineup[9] == BE
    _assert_legal(players, lineup)


def test_a_position_puzzle_greedy_gets_wrong():
    # Greedy by value hands the PF/C the C slot, and the C-only player is stranded with only
    # PF left. Exact: the C-only takes C, the PF/C takes PF.
    players = [_p(1, "PF/C", 40), _p(2, "C", 30)]
    assert best_lineup(players, {PF: 1, C: 1, BE: 5}) == {1: PF, 2: C}
    # The prompt's version: one C and one UT open. Both start.
    lineup = best_lineup(players, {C: 1, UT: 1, BE: 5})
    assert set(_starters(lineup)) == {1, 2}


def test_the_puzzle_inside_a_full_lineup():
    players = [
        _p(1, "PG", 50),
        _p(2, "SG", 45),
        _p(3, "SF", 40),
        _p(4, "PF/C", 38),
        _p(5, "C", 30),
        _p(6, "PF", 35),
        _p(7, "PG", 20),
        _p(8, "PG", 10),
    ]
    lineup = best_lineup(players, COUNTS)

    assert set(_starters(lineup)) == {1, 2, 3, 4, 5, 6, 7}
    assert lineup[5] == C and lineup[8] == BE
    _assert_legal(players, lineup)


def test_fewer_players_than_slots_fill_with_the_best_non_players():
    players = [
        _p(1, "PG", 30),
        _p(2, "C", 20),
        _p(3, "SF", 50, game=False),
        _p(4, "SG", 40, game=False),
        _p(5, "PF", 10, game=False),
        _p(6, "SG", 5, game=False),
    ]
    lineup = best_lineup(players, COUNTS)

    assert _starters(lineup) == {1: PG, 2: C, 3: SF, 4: SG, 5: PF, 6: UT}
    _assert_legal(players, lineup)


def test_leave_empty_starts_only_players_who_play():
    players = [_p(1, "PG", 30), _p(2, "C", 20), _p(3, "SF", 50, game=False)]
    lineup = best_lineup(players, COUNTS, leave_empty=True)

    assert lineup == {1: PG, 2: C, 3: BE}


def test_a_non_player_never_starts_over_a_benched_player_who_plays():
    # The no-game SF is worth far more, but the playing SF gets the slot.
    players = [_p(1, "SF", 99, game=False, slot=SF), _p(2, "SF", 1)]
    for leave_empty in (False, True):
        lineup = best_lineup(players, COUNTS, leave_empty=leave_empty)
        assert lineup[2] in (SF, UT)
        assert lineup[1] in ((UT, SF) if not leave_empty else (BE,))
    # Seven playing guards and a no-game star: the star sits.
    guards = [_p(10 + i, "PG/SG", 10 - i) for i in range(7)]
    lineup = best_lineup([*guards, _p(1, "PG", 99, game=False)], COUNTS)
    assert lineup[1] == BE


def test_out_and_suspended_with_a_game_dont_play_but_day_to_day_does():
    players = [
        _p(1, "PG", 90, injury="OUT"),
        _p(2, "PG", 80, injury="SUSPENSION"),
        _p(3, "PG", 10, injury="DAY_TO_DAY"),
        _p(4, "PG", 5),
    ]
    assert not players[0].plays and not players[1].plays
    assert players[2].plays and players[3].plays

    lineup = best_lineup(players, {PG: 1, UT: 1, BE: 5}, leave_empty=True)
    assert lineup == {1: BE, 2: BE, 3: PG, 4: UT}


def test_ir_is_untouched():
    players = [
        _p(1, "PG", 99, slot=IR, injury="OUT"),
        _p(2, "SG", 99, slot=IR),  # healthy on IR still stays there
        _p(3, "PG", 10),
    ]
    lineup = best_lineup(players, COUNTS)
    assert lineup[1] == IR and lineup[2] == IR
    assert lineup[3] == PG


def test_locked_players_keep_their_slots_and_the_rest_fill_around_them():
    players = [
        _p(1, "PG", 10, slot=PG),  # locked in PG though worse
        _p(2, "PG", 50, slot=BE),
        _p(3, "SF", 60, slot=BE),  # locked on the bench
        _p(4, "SF", 20, slot=BE),
        _p(5, "C", 30, slot=UT),  # locked in a UT
        _p(6, "C", 25, slot=BE),
    ]
    lineup = best_lineup(players, COUNTS, locked={1, 3, 5})

    assert lineup[1] == PG and lineup[3] == BE and lineup[5] == UT
    # PG taken: the better PG goes to the one UT left; the C-only to C; the SF to SF.
    assert lineup[2] == UT and lineup[6] == C and lineup[4] == SF
    _assert_legal(players, lineup)


def test_ties_go_to_the_lower_id_and_are_deterministic():
    players = [_p(7, "PG", 20), _p(3, "PG", 20), _p(5, "PG", 20)]
    counts = {PG: 1, UT: 1, BE: 5}
    first = best_lineup(players, counts)
    assert first == best_lineup(list(reversed(players)), counts)
    assert first[3] == PG and first[5] == UT and first[7] == BE


def test_value_never_trades_away_a_playing_starter():
    players = [_p(1, "PG", 1), _p(2, "PG", 1), _p(3, "PG", 100, game=False)]
    lineup = best_lineup(players, {PG: 1, UT: 1, BE: 5})
    assert _starters(lineup) == {1: PG, 2: UT}
