"""Every string the Team tab prints about a time. Pure: no database, no clock.

Times travel as ISO UTC everywhere else; these are the only place they turn into words, and
the words are always Pacific (`app.team.calendar.PACIFIC`), so a game time and a waiver clear
can never disagree about which zone they're in.
"""

from dataclasses import dataclass
from datetime import datetime

from app.team.calendar import PACIFIC


@dataclass(frozen=True)
class GameInfo:
    """One team's game on one day, from that team's side."""

    game_id: int
    sp: int
    opponent_pro_team_id: int
    # espn_api's PRO_TEAM_MAP abbreviation, the same one `Player.nba_team` carries.
    opponent: str
    is_home: bool
    starts_at: datetime
    start_time_tbd: bool


def pacific(moment: datetime) -> datetime:
    """An aware instant as Pacific wall-clock time."""
    if moment.tzinfo is None:
        raise ValueError("display helpers need timezone-aware datetimes")
    return moment.astimezone(PACIFIC)


def format_clock(moment: datetime) -> str:
    """`7:00 PM`: Pacific, 12-hour, no leading zero."""
    local = pacific(moment)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def format_matchup(info: GameInfo) -> str:
    """The game cell's first line: `@ LAL` away, `vs LAL` home."""
    return f"{'vs' if info.is_home else '@'} {info.opponent}"


def format_time(info: GameInfo) -> str:
    """The game cell's second line: `7:00 PM` Pacific, or `TBD` with no time set."""
    return "TBD" if info.start_time_tbd else format_clock(info.starts_at)


def format_game(info: GameInfo) -> str:
    """ESPN's game column on one line: `@ LAL 7:00 PM` away, `vs LAL 7:00 PM` home,
    `@ LAL TBD` with no time. The two halves are `format_matchup` and `format_time`."""
    return f"{format_matchup(info)} {format_time(info)}"


def format_clears_at(moment: datetime) -> str:
    """`clears Thu 10/22`: the Pacific date a waiver claim window closes."""
    local = pacific(moment)
    return f"clears {local:%a} {local.month}/{local.day}"
