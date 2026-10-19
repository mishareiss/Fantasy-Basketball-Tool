"""The Team tab's wire shapes. Times are ISO UTC; every `display` string is Pacific.

A stat line is players.py's `SeasonLine`, extended rather than re-invented: the same season /
games / fantasy points / per-game map, with the same rule that a stat nobody published is
absent rather than 0, plus where a `projected` line really came from.
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
# When on its day a move is made: after the day's games tip off, or before them.
PlanMoveTiming = Literal["after_tipoff", "before_games"]
PoolStatus = Literal["FREEAGENT", "WAIVERS"]

# Where a `projected` line came from: ESPN's projection for this season; last season's
# actual line, standing in because ESPN hasn't projected him this season; or an older season's
# projection, when there is no usable actual either.
ProjectionSource = Literal["espn", "last_season_actual", "stale_projection"]


class StatLine(SeasonLine):
    # Set on `projected` lines only; null on the actual bases.
    projection_source: ProjectionSource | None = None
    # The season the projected line is FOR (equals `season`; named so the UI can't confuse it
    # with the season being played).
    projection_season: int | None = None
    # Stats in `per_game` WE estimated because the source doesn't project them (DD, TD, ...).
    imputed: list[str] = []


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
    # Dropped tonight by an after-tip-off move: he still plays today, then leaves.
    leaving_move_id: int | None = None


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
    # "Wk G": his games from this day through the end of its matchup (inclusive). In the Plan
    # view, only days he's on the plan roster: a pickup from the day he lands, a drop through
    # his last day.
    matchup_games_left: int | None = None


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


class JoiningOut(BaseModel):
    """A pickup an after-tip-off move adds tonight: on the plan roster from tomorrow."""

    player: PlanPlayerRef
    move_id: int
    # His backups still in play ("or B").
    alternatives: list[PlanPlayerRef] = []


class PlanWarningOut(BaseModel):
    """One rule the plan breaks. Rules warn; they never block anything."""

    # Stable: acq_limit, on_waivers, drop_not_rostered, pickup_already_rostered,
    # double_pickup, bad_chain, chain_cycle, backup_overlap, stream_no_games, stream_info,
    # drop_game_lost, overdue, different_drop.
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
    # Plan view only: after-tip-off moves made tonight. Who leaves is still on the roster
    # today (and flagged `plan.leaving_move_id`); who joins is on it from tomorrow.
    leaving_tonight: list[PlannedDropOut] = []
    joining_tonight: list[JoiningOut] = []


class LineupAssignmentIn(BaseModel):
    player_id: int
    slot_id: int


class LineupEditRequest(BaseModel):
    assignments: list[LineupAssignmentIn]
    # Write the same lineup to every day from `sp` through this one (inclusive).
    through_sp: int | None = None


class LineupEditResponse(TeamDayResponse):
    days_written: int


class AutoFillRequest(BaseModel):
    sp: int
    # Auto-fill every day from `sp` through this one (inclusive), each from its own games.
    through_sp: int | None = None
    # Leave starting slots nobody playing can fill empty, rather than filling them with the
    # best players who aren't playing.
    leave_empty: bool = False


class AutoFillDay(BaseModel):
    sp: int
    starters_playing: int
    slots: int
    # Players who play that day but sit, because more play than there are starting slots
    # (or because his game had started on the bench, which locks him there).
    benched_playing: list[str]


class AutoFillResponse(LineupEditResponse):
    days: list[AutoFillDay]
    # False when every day's auto-filled lineup is the one it already had.
    changed: bool


class WaiverOut(BaseModel):
    clears_at: datetime | None = None
    # `clears Thu 10/22`, Pacific. Null when the clear time isn't known.
    clears_at_display: str | None = None
    clears_at_known: bool


class StatSummary(BaseModel):
    """The one number the waiver list prints, and how many games it is over."""

    fantasy_ppg: float
    games: float | None = None
    # As on `StatLine`: where a `projected` number came from. Null on the actual bases.
    projection_source: ProjectionSource | None = None
    projection_season: int | None = None


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
    # "Wk G": his games from today through the end of today's matchup, the roster's yardstick.
    matchup_games_left: int | None = None
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


class StreamGameDay(BaseModel):
    sp: int
    date: date
    # `@ LAL 7:00 PM`, Pacific.
    display: str


class StreamMatchupGames(BaseModel):
    matchup_id: int
    games: int


class StreamOut(BaseModel):
    """The days the move's pickup is mine in the plan, and his games in them: from the day he
    lands through the day before the move that drops him lands, else through the plan's
    last day (`open_ended`). `last_sp < first_sp` when he's dropped as soon as he arrives."""

    first_sp: int
    last_sp: int
    open_ended: bool
    # The move that ends the window (drops him); null when open-ended.
    ended_by_move_id: int | None = None
    game_days: list[StreamGameDay]
    games: int
    games_by_matchup: list[StreamMatchupGames]


class PlanMoveOut(BaseModel):
    id: int
    # The day the move is MADE (its acquisition and its drop's waiver clock are that day's).
    sp: int
    date: date
    matchup_id: int | None = None
    sequence: int
    status: PlanMoveStatus
    timing: PlanMoveTiming
    # The day it changes the plan roster: `sp` (made before games) or the day after (made
    # after tip-off), from today for an overdue move. Null unless planned.
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
    # Null for a skipped move, or one with no pickup.
    stream: StreamOut | None = None


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
    # When on `sp` it's made. New moves default to after tip-off.
    timing: PlanMoveTiming = "after_tipoff"


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
    timing: PlanMoveTiming | None = None


class AcquisitionOverrideIn(BaseModel):
    matchup_id: int
    # Null clears the override (ESPN's count applies again).
    used: int | None = Field(..., ge=0)


# --- the starts tracker ---------------------------------------------------------------------


class StartsPlayerRef(BaseModel):
    id: int
    name: str


class StartsDayOut(BaseModel):
    sp: int
    date: date
    weekday: str
    # Before today. Computed the same way as any other day (box-score starts come later).
    past: bool
    slots: int
    # Starting slots held by a player who plays, in the lineup as set.
    filled: int
    # Starting slots the best lineup from this day's roster fills with players who play.
    possible: int
    # slots - possible: nobody on the roster can fill them. Streaming opportunities.
    empty: int
    # possible - filled: starts the lineup as set leaves on the bench. Auto-fill fixes them.
    left_on_bench: int
    # Players who play but can't start: every slot that fits them is taken. Best first.
    benched_with_games: list[StartsPlayerRef]
    # The best lineup's starters' FPTS/G summed (auto-fill's value rule), and the lineup as set's.
    projected_points: float
    projected_points_as_set: float


class StartsTotals(BaseModel):
    days: int
    # slots x days.
    max: int
    filled: int
    possible: int
    empty: int
    left_on_bench: int
    projected_points: float
    projected_points_as_set: float


class StartsDelta(BaseModel):
    """The Plan view minus the Actual view, over the same matchup."""

    filled: int
    possible: int
    projected_points: float
    projected_points_as_set: float


class StartsResponse(BaseModel):
    season: int
    matchup: MatchupRef
    view: DayView
    days: list[StartsDayOut]
    totals: StartsTotals
    # Plan view only: the Actual view's totals for the same matchup, and the plan's gain.
    actual_totals: StartsTotals | None = None
    vs_actual: StartsDelta | None = None
