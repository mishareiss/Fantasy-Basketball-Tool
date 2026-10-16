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
# The Actual view's sources, then the Plan view's: plan-edited for this day, carried from an
# earlier plan-edited day, or the Actual lineup with each planned pickup in his drop's slot.
LineupSource = Literal[
    "espn", "manual", "carried", "roster", "plan_edit", "plan_carried", "from_actual"
]
DayView = Literal["actual", "plan"]
PlanMoveStatus = Literal["planned", "done", "skipped"]
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


class PlanPlayerRef(BaseModel):
    """A player as the waiver plan names him."""

    id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    injury_status: str | None = None


class PlanFlag(BaseModel):
    """A roster player in the Plan view: already mine, or a planned pickup (and his backups)."""

    status: Literal["actual", "planned"]
    move_id: int | None = None
    alternatives: list[PlanPlayerRef] = []
    # ESPN shows a pending waiver claim of ours for one of the move's candidates.
    claim_pending: bool = False


class RosterPlayer(BaseModel):
    id: int
    name: str
    nba_team: str | None = None
    positions: list[str] = []
    eligible_slots: list[int] = []
    injury_status: str | None = None
    game: GameOut | None = None
    stats: StatLine | None = None
    # Plan view only; null in the Actual view.
    plan: PlanFlag | None = None


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
    # `override` when a hand-set count replaces ESPN's for this matchup.
    used_source: Literal["espn", "override"] = "espn"
    # Planned (not yet made) pickups landing in this matchup.
    planned: int = 0


class PlannedDropOut(BaseModel):
    player: PlanPlayerRef
    move_id: int
    # For a chained drop not yet settled: who else it could turn out to be ("A (or B)").
    alternatives: list[PlanPlayerRef] = []


class PlanWarningOut(BaseModel):
    """One rule the plan breaks. Rules warn; they never block anything."""

    # Stable: acq_limit, on_waivers, drop_not_rostered, pickup_already_rostered,
    # double_pickup, bad_chain, chain_cycle, backup_overlap, no_game, overdue, different_drop.
    code: str
    severity: Literal["warning", "info"]
    message: str
    move_id: int | None = None
    matchup_id: int | None = None
    sp: int | None = None
    detail: dict = {}


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
    # The earlier day a carried lineup comes from (`carried`, `plan_carried`); else null.
    carried_from_sp: int | None = None
    carried_from_date: date | None = None
    acquisitions: Acquisitions
    slots: list[SlotRow]
    warnings: list[str] = []
    view: DayView = "actual"
    # Plan view only: who the plan drops on this day, and the plan's warnings for this day
    # and its matchup.
    planned_drops: list[PlannedDropOut] = []
    plan_warnings: list[PlanWarningOut] = []


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
    # Every planned move he's a candidate in.
    in_plan: list["InPlanRef"] = []


class InPlanRef(BaseModel):
    move_id: int
    sp: int
    # 1 = the move's primary pickup, 2.. a backup.
    rank: int


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


# --- the waiver plan ------------------------------------------------------------------------


class PlanDropOut(BaseModel):
    """Who a move drops: a named player, or whoever another move picks up."""

    player: PlanPlayerRef | None = None
    # A chained drop still unsettled: the referenced move's backups.
    alternatives: list[PlanPlayerRef] = []
    from_move_id: int | None = None
    # Set when a chained drop can't be trusted: missing, later, skipped or cycle.
    problem: Literal["missing", "later", "skipped", "cycle"] | None = None
    # `Jerami Grant`, or `Jerami Grant (or Bam Adebayo)` for an unsettled chain.
    display: str


class PlanCandidateOut(BaseModel):
    rank: int
    player: PlanPlayerRef


class PlanMoveOut(BaseModel):
    id: int
    sp: int
    date: date
    matchup_id: int | None = None
    sequence: int
    status: PlanMoveStatus
    # The day it takes effect in the plan (today for an overdue move). Null unless planned.
    lands_sp: int | None = None
    overdue: bool = False
    drop: PlanDropOut
    candidates: list[PlanCandidateOut]
    # The effective pickup (resolved candidate, else the primary) and the backups still open.
    pickup: PlanPlayerRef | None = None
    alternatives: list[PlanPlayerRef] = []
    resolved_player_id: int | None = None
    claim_pending: bool = False
    espn_transaction_id: str | None = None
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class AcquisitionRow(BaseModel):
    matchup_id: int
    label: str
    used: int
    used_source: Literal["espn", "override"]
    planned: int
    limit: int | None = None
    over: bool


class PlanResponse(BaseModel):
    season: int
    today_sp: int
    # Plan order: (sp, sequence, id).
    moves: list[PlanMoveOut]
    warnings: list[PlanWarningOut]
    # The current matchup through the last one any move touches.
    acquisitions: list[AcquisitionRow]


class PlanMutationResponse(PlanResponse):
    # The move created or changed (null after an override or a delete).
    move_id: int | None = None


class PlanDeleteResponse(PlanResponse):
    deleted_move_id: int
    # Moves whose chained drop was turned into a named drop first (`?repoint=true`).
    repointed: list[int] = []


class PlanMoveCreate(BaseModel):
    sp: int
    # Exactly one of these two.
    drop_player_id: int | None = None
    drop_from_move_id: int | None = None
    # Rank order: the primary first, then the backups.
    candidate_ids: list[int]
    note: str | None = Field(None, max_length=500)
    # Order among the day's moves. Default: last.
    sequence: int | None = None


class PlanMovePatch(BaseModel):
    """Any subset. A field sent as null clears it where that makes sense; one left out is kept."""

    sp: int | None = None
    sequence: int | None = None
    drop_player_id: int | None = None
    drop_from_move_id: int | None = None
    # Replaces the ordered list.
    candidate_ids: list[int] | None = None
    status: PlanMoveStatus | None = None
    resolved_player_id: int | None = None
    note: str | None = Field(None, max_length=500)


class AcquisitionOverrideIn(BaseModel):
    matchup_id: int
    # Null clears the override (ESPN's count applies again).
    used: int | None = Field(..., ge=0)
