"""The starts tracker's read: `GET /team/starts`. Loads one matchup's days in one batch and
runs each through the pure `app.team.starts`.

A fixed handful of queries however many days the matchup has: the schedule for every day is
one, each lineup table's rows for the whole range are two (`lineup_rows_through`), and the
values are auto-fill's (`player_values`). Nothing here writes.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import LineupAssignment, PlanLineupAssignment, Player
from app.team.autofill import AutoFillPlayer
from app.team.autofill_views import player_values
from app.team.calendar import (
    Matchup,
    clamp_period,
    effective_period,
    local_today,
    matchup_by_id,
    matchup_for_period,
    period_date,
    period_index,
)
from app.team.display import GameInfo
from app.team.lineup import BENCH_SLOT, resolve_lineup
from app.team.plan_views import load_plan, plan_day_placement
from app.team.schemas import (
    DayView,
    MatchupRef,
    StartsDayOut,
    StartsDelta,
    StartsPlayerRef,
    StartsResponse,
    StartsTotals,
)
from app.team.starts import DayStarts, MatchupStarts, day_starts, matchup_starts
from app.team.views import PeriodOutOfRange, _require_team, games_index, lineup_rows_through


def _matchup(season: int, matchup_id: int | None, now: datetime) -> Matchup:
    if matchup_id is None:
        matchup = matchup_for_period(season, effective_period(season, now))
        assert matchup is not None  # the effective period is always inside the calendar
        return matchup
    try:
        return matchup_by_id(season, matchup_id)
    except ValueError as exc:
        raise PeriodOutOfRange(str(exc)) from exc


def _candidates(
    sp: int,
    players: Mapping[int, Player],
    placement: Mapping[int, int],
    games: Mapping[tuple[int, int], GameInfo],
    values: Mapping[int, float],
) -> list[AutoFillPlayer]:
    return [
        AutoFillPlayer(
            id=pid,
            eligible_slots=list(player.eligible_slots or []),
            slot_id=placement.get(pid, BENCH_SLOT),
            has_game=(sp, player.pro_team_id) in games,
            value=values.get(pid, 0.0),
            injury_status=player.injury_status,
        )
        for pid, player in players.items()
    ]


def _totals(totals: MatchupStarts) -> StartsTotals:
    return StartsTotals(
        days=totals.days,
        max=totals.max,
        filled=totals.filled,
        possible=totals.possible,
        empty=totals.empty,
        left_on_bench=totals.left_on_bench,
        projected_points=totals.projected_points,
        projected_points_as_set=totals.projected_points_as_set,
    )


def _day_out(season: int, day: DayStarts, names: Mapping[int, Player]) -> StartsDayOut:
    d = period_date(season, day.sp)
    return StartsDayOut(
        sp=day.sp,
        date=d,
        weekday=f"{d:%a}",
        past=day.past,
        slots=day.slots,
        filled=day.filled,
        possible=day.possible,
        empty=day.empty,
        left_on_bench=day.left_on_bench,
        benched_with_games=[
            StartsPlayerRef(id=pid, name=names[pid].full_name) for pid in day.benched_with_games
        ],
        projected_points=day.projected_points,
        projected_points_as_set=day.projected_points_as_set,
    )


def team_starts(
    db: Session, season: int, matchup_id: int | None, view: DayView, now: datetime
) -> StartsResponse:
    """Starts filled vs possible on every day of one matchup (default: the current one), in
    the Actual or the Plan view. The Plan view also carries the Actual totals and its delta."""
    team = _require_team(db, season)
    matchup = _matchup(season, matchup_id, now)
    first = clamp_period(season, period_index(season, matchup.start))
    last = clamp_period(season, period_index(season, matchup.end))
    days: Sequence[int] = range(first, last + 1)
    today = period_index(season, local_today(now))

    bundle = load_plan(db, season, team, now, through_sp=last)
    games = games_index(db, season, days)
    values = player_values(db, season, bundle.players)
    actual_rows = lineup_rows_through(db, LineupAssignment, season, team.team_id, first, last)
    roster = {player.espn_player_id: player for _, player in bundle.roster}
    roster_slots = {entry.player_id: entry.lineup_slot_id for entry, _ in bundle.roster}
    actual_placements = {sp: resolve_lineup(actual_rows, roster_slots, sp) for sp in days}

    actual_days = [
        day_starts(
            sp,
            _candidates(sp, roster, actual_placements[sp], games, values),
            bundle.slot_counts,
            past=sp < today,
        )
        for sp in days
    ]
    shown = actual_days
    actual_totals = matchup_starts(actual_days)
    totals = actual_totals
    delta: StartsDelta | None = None

    if view == "plan":
        plan_rows = lineup_rows_through(db, PlanLineupAssignment, season, team.team_id, first, last)
        shown = []
        for sp in days:
            players, placement, _, _ = plan_day_placement(
                bundle, sp, actual_placements[sp], plan_rows
            )
            shown.append(
                day_starts(
                    sp,
                    _candidates(sp, players, placement, games, values),
                    bundle.slot_counts,
                    past=sp < today,
                )
            )
        totals = matchup_starts(shown)
        delta = StartsDelta(
            filled=totals.filled - actual_totals.filled,
            possible=totals.possible - actual_totals.possible,
            projected_points=round(totals.projected_points - actual_totals.projected_points, 2),
            projected_points_as_set=round(
                totals.projected_points_as_set - actual_totals.projected_points_as_set, 2
            ),
        )

    return StartsResponse(
        season=season,
        matchup=MatchupRef(
            id=matchup.id, label=matchup.label, start=matchup.start, end=matchup.end
        ),
        view=view,
        days=[_day_out(season, day, bundle.players) for day in shown],
        totals=_totals(totals),
        actual_totals=_totals(actual_totals) if view == "plan" else None,
        vs_actual=delta,
    )
