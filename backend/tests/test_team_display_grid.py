"""The Team tab's pure helpers: Pacific display strings, the slot grid, the lineup-edit rules
and the waiver list's day filter."""

from datetime import UTC, datetime

import pytest

from app.team.display import GameInfo, format_clears_at, format_clock, format_game
from app.team.grid import (
    LineupCandidate,
    build_slot_grid,
    passes_day_filter,
    slot_counts_by_id,
    validate_lineup,
)

# Our league: PG SG SF PF C, UT x2, BE x10, IR x3 (lineupSlotCounts by name).
LEAGUE_SLOTS = {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, "UT": 2, "BE": 10, "IR": 3}
PG, SG, SF, PF, C, UT, BE, IR = 0, 1, 2, 3, 4, 11, 12, 13


def _game(starts_at: datetime, *, home: bool = False, tbd: bool = False) -> GameInfo:
    return GameInfo(
        game_id=1,
        sp=1,
        opponent_pro_team_id=13,
        opponent="LAL",
        is_home=home,
        starts_at=starts_at,
        start_time_tbd=tbd,
    )


# --- display --------------------------------------------------------------------------------


def test_an_october_afternoon_game_is_pdt():
    # 19:00Z on Oct 21 = noon PDT (UTC-7).
    assert format_game(_game(datetime(2026, 10, 21, 19, 0, tzinfo=UTC))) == "@ LAL 12:00 PM"


def test_home_games_read_vs():
    assert format_game(_game(datetime(2026, 10, 22, 2, 0, tzinfo=UTC), home=True)) == (
        "vs LAL 7:00 PM"
    )


def test_a_game_after_the_fall_back_is_pst():
    # Nov 5 03:00Z = Nov 4 7:00 PM PST (UTC-8). Under PDT it would have read 8:00 PM.
    assert format_game(_game(datetime(2026, 11, 5, 3, 0, tzinfo=UTC))) == "@ LAL 7:00 PM"


def test_a_utc_morning_game_is_the_previous_pacific_evening():
    # 02:00Z on Oct 22 is still Oct 21 in Pacific: 7:00 PM, no leading zero.
    moment = datetime(2026, 10, 22, 2, 0, tzinfo=UTC)
    assert format_clock(moment) == "7:00 PM"
    assert format_game(_game(moment)) == "@ LAL 7:00 PM"


def test_tbd_games_have_no_time():
    assert format_game(_game(datetime(2026, 10, 22, 2, 0, tzinfo=UTC), tbd=True)) == "@ LAL TBD"


def test_clock_edges():
    assert format_clock(datetime(2026, 10, 21, 7, 0, tzinfo=UTC)) == "12:00 AM"
    assert format_clock(datetime(2026, 10, 21, 16, 30, tzinfo=UTC)) == "9:30 AM"


def test_clears_at_reads_as_the_pacific_date():
    # 2026-10-22 00:00 PDT = 07:00Z; a Thursday.
    assert format_clears_at(datetime(2026, 10, 22, 7, 0, tzinfo=UTC)) == "clears Thu 10/22"


def test_display_refuses_naive_times():
    with pytest.raises(ValueError):
        format_clock(datetime(2026, 10, 21, 19, 0))


# --- the slot grid --------------------------------------------------------------------------


def test_slot_counts_convert_names_to_ids_and_drop_zeroes():
    assert slot_counts_by_id(LEAGUE_SLOTS | {"G": 0}) == {
        PG: 1,
        SG: 1,
        SF: 1,
        PF: 1,
        C: 1,
        UT: 2,
        BE: 10,
        IR: 3,
    }
    assert slot_counts_by_id(None) == {}


def test_the_grid_is_in_espn_order_with_empty_slots_as_rows():
    counts = slot_counts_by_id(LEAGUE_SLOTS)
    rows, warnings = build_slot_grid(counts, {101: PG, 102: UT, 103: BE})

    assert warnings == []
    assert [row.slot for row in rows] == (
        ["PG", "SG", "SF", "PF", "C", "UT", "UT"] + ["BE"] * 10 + ["IR"] * 3
    )
    assert rows[0].player_id == 101
    assert rows[1].player_id is None  # SG: empty, still a row
    assert [row.player_id for row in rows if row.slot == "UT"] == [102, None]
    assert [row.player_id for row in rows if row.slot == "BE"][:2] == [103, None]


def test_the_bench_grows_to_fit_the_roster():
    counts = slot_counts_by_id(LEAGUE_SLOTS)
    placement = {pid: BE for pid in range(1, 13)}  # 12 on a 10-man bench

    rows, warnings = build_slot_grid(counts, placement)

    bench = [row for row in rows if row.slot == "BE"]
    assert len(bench) == 12
    assert [row.player_id for row in bench] == list(range(1, 13))
    assert warnings == []
    # IR stays after the bench.
    assert [row.slot for row in rows[-3:]] == ["IR"] * 3


def test_an_overfull_slot_spills_onto_the_end_of_the_bench_with_a_warning():
    counts = slot_counts_by_id(LEAGUE_SLOTS)
    rows, warnings = build_slot_grid(counts, {1: PG, 2: PG, 3: BE, 4: 5}, order=[1, 2, 3, 4])

    assert [row.player_id for row in rows if row.slot == "PG"] == [1]
    assert [row.player_id for row in rows if row.slot == "BE"][:3] == [3, 2, 4]
    assert len(warnings) == 2
    assert "PG" in warnings[0] and "G" in warnings[1]
    # Nobody vanished.
    assert {row.player_id for row in rows} - {None} == {1, 2, 3, 4}


def test_the_order_argument_fills_multi_row_slots():
    counts = slot_counts_by_id(LEAGUE_SLOTS)
    rows, _ = build_slot_grid(counts, {1: UT, 2: UT}, order=[2, 1])
    assert [row.player_id for row in rows if row.slot == "UT"] == [2, 1]


# --- lineup-edit rules ----------------------------------------------------------------------


def _roster() -> dict[int, LineupCandidate]:
    guard = [PG, SG, 5, UT, BE, IR]
    big = [C, PF, UT, BE, IR]
    return {
        1: LineupCandidate(1, "Guard One", guard, "ACTIVE"),
        2: LineupCandidate(2, "Guard Two", guard, "OUT"),
        3: LineupCandidate(3, "Big Three", big, None),
        4: LineupCandidate(4, "Big Four", big, "DAY_TO_DAY"),
    }


def test_a_legal_lineup_has_no_errors():
    errors, warnings = validate_lineup(
        [(1, PG), (2, IR), (3, C), (4, UT)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert errors == []
    assert warnings == []


def test_missing_and_extra_players_are_errors():
    errors, _ = validate_lineup(
        [(1, PG), (2, SG), (3, C), (99, BE)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert any("player 99 is not on the roster" in e for e in errors)
    assert any("Big Four (4) is missing" in e for e in errors)


def test_a_player_listed_twice_is_an_error():
    errors, _ = validate_lineup(
        [(1, PG), (1, BE), (2, SG), (3, C), (4, PF)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert any("assigned 2 times" in e for e in errors)


def test_a_slot_over_capacity_is_an_error():
    errors, _ = validate_lineup(
        [(1, PG), (2, PG), (3, C), (4, PF)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert errors == ["2 players in PG, which has room for 1"]


def test_an_ineligible_slot_is_an_error():
    errors, _ = validate_lineup(
        [(1, C), (2, SG), (3, PF), (4, BE)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert errors == ["Guard One (1) can't play C"]


def test_a_slot_the_league_lacks_is_over_capacity():
    # Guards are G-eligible (5), but our league has no G slot.
    errors, _ = validate_lineup(
        [(1, 5), (2, SG), (3, C), (4, PF)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert errors == ["1 players in G, which has room for 0"]


def test_the_bench_is_always_allowed_up_to_roster_size():
    roster = _roster()
    roster[5] = LineupCandidate(5, "No Slots", None, None)  # eligibility never synced
    errors, _ = validate_lineup([(pid, BE) for pid in roster], roster, slot_counts_by_id({"BE": 1}))
    assert errors == []


def test_a_healthy_player_on_ir_is_a_warning_not_an_error():
    errors, warnings = validate_lineup(
        [(1, IR), (2, SG), (3, IR), (4, C)], _roster(), slot_counts_by_id(LEAGUE_SLOTS)
    )
    assert errors == []
    assert warnings == [
        "Guard One (1) is on IR but not listed as injured",
        "Big Three (3) is on IR but not listed as injured",
    ]


# --- the day filter -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "mode", "expected"),
    [
        ([False], "none", True),
        ([True], "any", True),
        ([False], "any", False),
        ([True], "all", True),
        ([False], "all", False),
        ([True, False, False], "any", True),
        ([False, False, False], "any", False),
        ([True, True, False], "all", False),
        ([True, True, True], "all", True),
        ([False, False, False], "none", True),
        ([], "all", False),
    ],
)
def test_day_filter_modes(flags, mode, expected):
    assert passes_day_filter(flags, mode) is expected
