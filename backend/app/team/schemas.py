"""The Team tab's wire shapes. Times are ISO UTC; every `display` string is Pacific.

A stat line is players.py's `SeasonLine`, reused rather than re-invented: the same season /
games / fantasy points / per-game map, with the same rule that a stat nobody published is
absent rather than 0.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.api.players import SeasonLine

Basis = Literal["projected", "season", "l7", "l15", "l30"]
LineupSource = Literal["espn", "manual", "carried", "roster"]
PoolStatus = Literal["FREEAGENT", "WAIVERS"]

StatLine = SeasonLine


class TeamRef(BaseModel):
    id: int
    name: str
    abbrev: str | None = None


class MatchupRef(BaseModel):
    id: int
    label: str
    start: date
    end: date


class GameOut(BaseModel):
    """One player's game on one day, from his team's side, plus ESPN's game-column string."""

    game_id: int
    sp: int
    opponent_pro_team_id: int
    opponent: str
    is_home: bool
    starts_at: datetime
    start_time_tbd: bool
    # `@ LAL 7:00 PM` / `vs LAL 7:00 PM` / `@ LAL TBD`, Pacific.
    display: str
    # The same, split for a two-line cell: `@ LAL` / `vs LAL`, then `7:00 PM` / `TBD`.
    display_matchup: str
    display_time: str


class RosterPlayer(BaseModel):
    id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    eligible_slots: list[int] = []
    injury_status: str | None = None
    game: GameOut | None = None
    stats: StatLine | None = None


class SlotRow(BaseModel):
    slot_id: int
    slot: str
    # Null for an empty slot, which is still a row, as ESPN shows it.
    player: RosterPlayer | None = None


class Acquisitions(BaseModel):
    """Adds used in this day's matchup (ESPN's count) against the league's limit."""

    used: int
    limit: int | None = None
    matchup_id: int


class TeamDayResponse(BaseModel):
    season: int
    sp: int
    date: date
    matchup: MatchupRef
    is_today: bool
    team: TeamRef
    actuals_available: bool
    basis: Basis
    # The DISPLAY_STATS at least one returned line has, in print order.
    stat_columns: list[str]
    # Where the lineup came from: stored for this day (espn / manual), carried forward from
    # an earlier stored day, or the slots the last roster sync saw.
    lineup_source: LineupSource
    acquisitions: Acquisitions
    slots: list[SlotRow]
    warnings: list[str] = []


class LineupAssignmentIn(BaseModel):
    player_id: int
    slot_id: int


class LineupEditRequest(BaseModel):
    assignments: list[LineupAssignmentIn]
    # Write the same lineup to every day from `sp` through this one (inclusive).
    through_sp: int | None = None


class LineupEditResponse(TeamDayResponse):
    days_written: int


class WaiverOut(BaseModel):
    clears_at: datetime | None = None
    # `clears Thu 10/22`, Pacific. Null when the clear time isn't known.
    clears_at_display: str | None = None
    clears_at_known: bool


class StatSummary(BaseModel):
    """The one number the waiver list prints, and how many games it is over."""

    fantasy_ppg: float
    games: float | None = None


class DayGame(BaseModel):
    sp: int
    game: GameOut | None = None


class FreeAgentRow(BaseModel):
    id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    eligible_slots: list[int] = []
    injury_status: str | None = None
    status: PoolStatus
    # Null for a free agent; set for a player on waivers.
    waiver: WaiverOut | None = None
    # Aligned with the response's `days`.
    games: list[DayGame]
    games_in_days: int
    stats: StatSummary | None = None
    # On our shared watchlist this season.
    watched: bool = False


class DayRef(BaseModel):
    sp: int
    date: date
    matchup_id: int | None = None


class FreeAgentsResponse(BaseModel):
    season: int
    days: list[DayRef]
    mode: Literal["none", "any", "all"]
    basis: Basis
    actuals_available: bool
    # Matching players before `limit` / `offset`.
    total: int
    players: list[FreeAgentRow]


class OwnerTeam(BaseModel):
    team_id: int
    name: str


class CardPlayer(BaseModel):
    id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    eligible_slots: list[int] = []
    injury_status: str | None = None
    owner: PoolStatus | OwnerTeam


class WatchlistEntryOut(BaseModel):
    """A watched player, and who holds him now: a rostered one stays on the list."""

    player_id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    injury_status: str | None = None
    owner: PoolStatus | OwnerTeam
    added_at: datetime
    note: str | None = None


class WatchlistResponse(BaseModel):
    season: int
    # Newest first.
    entries: list[WatchlistEntryOut]


class WatchlistRemoved(BaseModel):
    player_id: int
    # Whether there was an entry to remove. False is not an error: the delete is idempotent.
    removed: bool


class WatchlistPutRequest(BaseModel):
    # Sent: replaces the note (null clears it). Left out: keeps whatever note is there.
    note: str | None = Field(None, max_length=500)


class CardStats(BaseModel):
    projected: StatLine | None = None
    season: StatLine | None = None
    l7: StatLine | None = None
    l15: StatLine | None = None
    l30: StatLine | None = None


class ScheduleDay(BaseModel):
    sp: int
    date: date
    weekday: str
    matchup_id: int | None = None
    matchup_label: str | None = None
    game: GameOut | None = None


class MatchupGroup(BaseModel):
    matchup_id: int
    label: str
    start: date
    end: date
    # The scoring periods of the window that fall in this matchup.
    days: list[int]
    games: int


class PlayerCardResponse(BaseModel):
    season: int
    player: CardPlayer
    waiver: WaiverOut | None = None
    # On our shared watchlist this season (the card's star).
    watched: bool = False
    actuals_available: bool
    stat_columns: list[str]
    stats: CardStats
    schedule: list[ScheduleDay]
    by_matchup: list[MatchupGroup]


class CalendarMatchup(BaseModel):
    id: int
    label: str
    start: date
    end: date
    first_sp: int
    last_sp: int


class CalendarDay(BaseModel):
    sp: int
    date: date
    matchup_id: int | None = None
    # How many of my rostered players have a game that day.
    my_games: int


class TeamCalendarResponse(BaseModel):
    season: int
    today_period: int | None = None
    effective_period: int
    final_period: int
    matchups: list[CalendarMatchup]
    days: list[CalendarDay]
