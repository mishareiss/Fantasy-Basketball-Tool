"""The fantasy calendar: days are periods, matchups cover them exactly, and "today" is Pacific."""

from datetime import UTC, date, datetime, timedelta

import pytest

from app.team.calendar import (
    FINAL_PERIOD,
    PACIFIC,
    UnknownSeasonError,
    default_lineup_range,
    effective_period,
    matchup_for_period,
    matchups,
    period_date,
    period_for,
    periods_in_matchup,
    today_period,
)

SEASON = 2027


def test_period_one_and_the_final_period_are_the_right_days():
    assert period_date(SEASON, 1) == date(2026, 10, 20)
    assert period_date(SEASON, 160) == date(2027, 3, 28)
    assert FINAL_PERIOD[SEASON] == 160


def test_period_date_and_period_for_invert_each_other():
    for sp in range(1, 161):
        assert period_for(SEASON, period_date(SEASON, sp)) == sp


def test_period_for_is_none_outside_the_fantasy_season():
    assert period_for(SEASON, date(2026, 10, 19)) is None
    assert period_for(SEASON, date(2027, 3, 29)) is None


def test_period_date_still_answers_past_the_fantasy_final():
    """ESPN's NBA schedule numbers the rest of the regular season the same way (to 174)."""
    assert period_date(SEASON, 174) == date(2027, 4, 11)
    with pytest.raises(ValueError):
        period_date(SEASON, 0)


def test_there_are_21_matchups_labelled_like_espn():
    all_matchups = matchups(SEASON)
    assert len(all_matchups) == 21
    assert [m.id for m in all_matchups] == list(range(1, 22))
    assert all_matchups[0].label == "Matchup 1"
    assert all_matchups[17].label == "Matchup 18"
    assert [m.label for m in all_matchups[18:]] == [
        "Playoff Round 1",
        "Playoff Round 2",
        "Playoff Round 3",
    ]


def test_matchups_are_contiguous_and_cover_exactly_the_fantasy_season():
    all_matchups = matchups(SEASON)
    assert all_matchups[0].start == period_date(SEASON, 1)
    assert all_matchups[-1].end == period_date(SEASON, 160)
    for before, after in zip(all_matchups, all_matchups[1:], strict=False):
        assert before.start <= before.end
        assert after.start == before.end + timedelta(days=1), (before, after)

    covered = [sp for m in all_matchups for sp in periods_in_matchup(SEASON, m.id)]
    assert covered == list(range(1, 161))


@pytest.mark.parametrize(
    ("sp", "matchup_id"),
    [
        (1, 1),
        (6, 1),  # Sun Oct 25
        (7, 2),  # Mon Oct 26
        (119, 18),  # Feb 15: the two-week All-Star matchup starts
        (132, 18),  # Feb 28: and ends
        (133, 19),  # Mar 1: Playoff Round 1
        (146, 20),  # Mar 14: Playoff Round 2 ends
        (147, 21),  # Mar 15: the two-week final
        (160, 21),
    ],
)
def test_matchup_for_period_at_the_boundaries(sp, matchup_id):
    assert matchup_for_period(SEASON, sp).id == matchup_id


def test_matchup_for_period_outside_the_season_is_none():
    assert matchup_for_period(SEASON, 0) is None
    assert matchup_for_period(SEASON, 161) is None


def test_the_two_week_matchups_have_fourteen_days():
    assert len(periods_in_matchup(SEASON, 18)) == 14
    assert len(periods_in_matchup(SEASON, 21)) == 14
    assert len(periods_in_matchup(SEASON, 1)) == 6  # Tue-Sun opening week


def test_today_is_the_pacific_date():
    # 11:30pm Pacific on opening night is already Oct 21 in UTC and in New York.
    late = datetime(2026, 10, 20, 23, 30, tzinfo=PACIFIC)
    assert late.astimezone(UTC).date() == date(2026, 10, 21)
    assert today_period(SEASON, late) == 1

    assert today_period(SEASON, datetime(2026, 10, 21, 0, 1, tzinfo=PACIFIC)) == 2


def test_today_period_is_none_before_tip_off_but_the_effective_period_is_one():
    preseason = datetime(2026, 10, 15, 12, 0, tzinfo=PACIFIC)
    assert today_period(SEASON, preseason) is None
    assert effective_period(SEASON, preseason) == 1
    after = datetime(2027, 4, 5, 12, 0, tzinfo=PACIFIC)
    assert effective_period(SEASON, after) == 160


def test_naive_now_is_refused():
    with pytest.raises(ValueError):
        today_period(SEASON, datetime(2026, 10, 20, 12, 0))


def test_the_default_lineup_window_runs_to_the_end_of_the_next_matchup():
    # Pre-season: from day 1 through the end of matchup 2 (Sun Nov 1 = period 13).
    assert default_lineup_range(SEASON, datetime(2026, 10, 15, 12, tzinfo=PACIFIC)) == (1, 13)
    # Mid-matchup 3 (Wed Nov 4 = period 16): through the end of matchup 4 (Nov 15 = 27).
    assert default_lineup_range(SEASON, datetime(2026, 11, 4, 12, tzinfo=PACIFIC)) == (16, 27)
    # In the final there is no next matchup: through the end of this one.
    assert default_lineup_range(SEASON, datetime(2027, 3, 20, 12, tzinfo=PACIFIC)) == (152, 160)


def test_an_unknown_season_raises_a_clear_error():
    with pytest.raises(UnknownSeasonError, match="No fantasy calendar for season 2031"):
        period_date(2031, 1)
    with pytest.raises(UnknownSeasonError):
        matchup_for_period(2031, 1)
    with pytest.raises(UnknownSeasonError):
        today_period(2031, datetime(2030, 11, 1, tzinfo=UTC))
