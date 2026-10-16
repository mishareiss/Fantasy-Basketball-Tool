"""The fantasy calendar: scoring periods (days), matchups, and what "today" is.

Pure on purpose: no database, no ESPN, no clock. Everything takes the season and a date or a
`now`, so the Team tab, the waiver plan and the tests all agree about which day it is.

Two facts carry the whole module, both checked against the live league for 2027:

* A scoring period is one calendar day in US/Eastern. Period 1 is ESPN's day one of the
  season and period N is day one plus N - 1 days, with no exceptions across every period that
  has games. So a period is a date and a date is a period; no table is needed.
* Matchup date ranges are NOT in the API. `mSettings.scheduleSettings.matchupPeriods` maps a
  matchup to internal week ids but carries no dates, and two of the "weeks" are two weeks long
  (the All-Star break and the final). The ranges below were copied from ESPN's league
  schedule page and are the source of truth. A new season needs a new entry. Guessing one
  from the week ids would be wrong exactly where it matters.

"Today" is the America/Los_Angeles date. The latest NBA tip is about 10:30pm ET, which is
7:30pm PT, so every game's Eastern date is also its Pacific date, and a Pacific user's
calendar day is the right one to file a game under.
"""

from datetime import date, datetime, time, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
EASTERN = ZoneInfo("America/New_York")


class UnknownSeasonError(KeyError):
    """No calendar for this season. Add one; never guess the weeks."""

    def __init__(self, season: int) -> None:
        super().__init__(season)
        self.season = season

    def __str__(self) -> str:
        return (
            f"No fantasy calendar for season {self.season}. Add its day one, final period and "
            "matchup date ranges to app/team/calendar.py (copied from ESPN's league schedule "
            "page). The API does not publish matchup dates."
        )


class Matchup(NamedTuple):
    """One head-to-head matchup: inclusive start and end dates (US/Eastern days)."""

    id: int
    start: date
    end: date
    label: str


# The date of scoring period 1, by season (ESPN labels a season by the year it ends).
SEASON_DAY_ONE: dict[int, date] = {2027: date(2026, 10, 20)}

# The last scoring period of the FANTASY season: the end of the fantasy playoffs, which is
# earlier than the end of the NBA regular season. ESPN's schedule keeps numbering periods past
# this one (to 174 in 2027), so `period_date` accepts them, but nothing fantasy happens there.
FINAL_PERIOD: dict[int, int] = {2027: 160}


def _matchups_2027() -> list[Matchup]:
    ranges = [
        ((10, 20), (10, 25)),
        ((10, 26), (11, 1)),
        ((11, 2), (11, 8)),
        ((11, 9), (11, 15)),
        ((11, 16), (11, 22)),
        ((11, 23), (11, 29)),
        ((11, 30), (12, 6)),
        ((12, 7), (12, 13)),
        ((12, 14), (12, 20)),
        ((12, 21), (12, 27)),
        ((12, 28), (1, 3)),
        ((1, 4), (1, 10)),
        ((1, 11), (1, 17)),
        ((1, 18), (1, 24)),
        ((1, 25), (1, 31)),
        ((2, 1), (2, 7)),
        ((2, 8), (2, 14)),
        ((2, 15), (2, 28)),  # All-Star break: two weeks
        ((3, 1), (3, 7)),
        ((3, 8), (3, 14)),
        ((3, 15), (3, 28)),  # the final: two weeks
    ]

    def day(month: int, dom: int) -> date:
        # October to December belong to the calendar year the season starts in.
        return date(2026 if month >= 10 else 2027, month, dom)

    out = []
    for index, (start, end) in enumerate(ranges, start=1):
        label = f"Matchup {index}" if index <= 18 else f"Playoff Round {index - 18}"
        out.append(Matchup(index, day(*start), day(*end), label))
    return out


MATCHUPS: dict[int, list[Matchup]] = {2027: _matchups_2027()}


def _day_one(season: int) -> date:
    try:
        return SEASON_DAY_ONE[season]
    except KeyError:
        raise UnknownSeasonError(season) from None


def final_period(season: int) -> int:
    """The last fantasy scoring period of `season`."""
    try:
        return FINAL_PERIOD[season]
    except KeyError:
        raise UnknownSeasonError(season) from None


def matchups(season: int) -> list[Matchup]:
    """Every matchup of `season`, in order."""
    try:
        return MATCHUPS[season]
    except KeyError:
        raise UnknownSeasonError(season) from None


def period_date(season: int, sp: int) -> date:
    """The calendar day scoring period `sp` is.

    Not capped at the final fantasy period: ESPN's NBA schedule numbers the rest of the regular
    season the same way, and checking those games' dates needs the same arithmetic.
    """
    if sp < 1:
        raise ValueError(f"Scoring periods start at 1, not {sp}")
    return _day_one(season) + timedelta(days=sp - 1)


def period_index(season: int, d: date) -> int:
    """`d` as a scoring period number, unclamped: <= 0 before the season, > final after it."""
    return (d - _day_one(season)).days + 1


def period_for(season: int, d: date) -> int | None:
    """The fantasy scoring period `d` falls in, or None outside the fantasy season."""
    sp = period_index(season, d)
    return sp if 1 <= sp <= final_period(season) else None


def clamp_period(season: int, sp: int) -> int:
    """`sp` pulled into `[1, final period]`."""
    return max(1, min(sp, final_period(season)))


def matchup_for_period(season: int, sp: int) -> Matchup | None:
    """The matchup scoring period `sp` belongs to, or None outside the fantasy season."""
    if sp < 1:
        return None
    d = period_date(season, sp)
    for matchup in matchups(season):
        if matchup.start <= d <= matchup.end:
            return matchup
    return None


def matchup_by_id(season: int, matchup_id: int) -> Matchup:
    for matchup in matchups(season):
        if matchup.id == matchup_id:
            return matchup
    raise ValueError(f"Season {season} has no matchup {matchup_id}")


def periods_in_matchup(season: int, matchup_id: int) -> range:
    """The scoring periods of one matchup, as a range (end inclusive in the dates, so +1)."""
    matchup = matchup_by_id(season, matchup_id)
    return range(period_index(season, matchup.start), period_index(season, matchup.end) + 1)


def local_today(now: datetime) -> date:
    """The America/Los_Angeles date of `now`. `now` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("`now` must be timezone-aware")
    return now.astimezone(PACIFIC).date()


def today_period(season: int, now: datetime) -> int | None:
    """Today's fantasy scoring period (Pacific date), or None outside the fantasy season."""
    return period_for(season, local_today(now))


def effective_period(season: int, now: datetime) -> int:
    """Today's period, clamped into the season: period 1 before it, the final one after it.

    What a "current" view should show when today is not a fantasy day: before tip-off the
    first matchup is the one coming up, and after the final the last one is the one that was.
    """
    return clamp_period(season, period_index(season, local_today(now)))


def default_lineup_range(season: int, now: datetime) -> tuple[int, int]:
    """Today's period through the last day of the NEXT matchup, clamped to the season.

    The window the "sync lineup from ESPN" pull covers by default: what is set for this
    matchup plus whatever has already been set for the next one.
    """
    first = effective_period(season, now)
    current = matchup_for_period(season, first)
    if current is None:  # unreachable for a calendar that covers 1..final, but be explicit
        return first, first
    following = [m for m in matchups(season) if m.id == current.id + 1]
    last_matchup = following[0] if following else current
    last = clamp_period(season, period_index(season, last_matchup.end))
    return first, last


def eastern_date(moment: datetime) -> date:
    """The US/Eastern calendar day of an aware instant: the day ESPN files a game under."""
    if moment.tzinfo is None:
        raise ValueError("`moment` must be timezone-aware")
    return moment.astimezone(EASTERN).date()


def local_midnight_after(moment: datetime, tz: ZoneInfo) -> datetime:
    """The first midnight in `tz` strictly after `moment`, as an aware datetime in `tz`."""
    local = moment.astimezone(tz)
    return datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz)
