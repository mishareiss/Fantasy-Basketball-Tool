"""Auto-fill for the Actual and Plan views: load each day, run the pure optimizer
(`app.team.autofill`), and write the result through the views' own save paths, so the same
validation applies and the rows land where a hand edit's would."""

from collections.abc import Iterable, Mapping
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.models import Player
from app.team.autofill import AutoFillPlayer, best_lineup, starting_slots
from app.team.calendar import today_period
from app.team.display import GameInfo
from app.team.grid import slot_counts_by_id
from app.team.lineup import BENCH_SLOT, IR_SLOT
from app.team.plan_views import _plan_day, _plan_day_response, load_plan, store_plan_lineups
from app.team.schemas import AutoFillDay, AutoFillResponse, Basis, TeamDayResponse
from app.team.store import league_settings_row
from app.team.views import (
    _require_team,
    _roster,
    actual_placement,
    check_range,
    games_index,
    stat_lines,
    store_lineups,
    team_day,
)


def player_values(db: Session, season: int, player_ids: Iterable[int]) -> dict[int, float]:
    """Each player's auto-fill value: this season's FPTS/G once he's played a game, else his
    projected FPTS/G, else 0. Two queries however many players."""
    wanted = list(set(player_ids))
    actual = stat_lines(db, season, wanted, "season")
    projected = stat_lines(db, season, wanted, "projected")
    out: dict[int, float] = {}
    for pid in wanted:
        line = actual.get(pid)
        if line is None or not (line.games or 0) > 0:
            line = projected.get(pid)
        out[pid] = line.fantasy_ppg if line is not None else 0.0
    return out


def _fill_day(
    sp: int,
    players: Mapping[int, Player],
    placement: Mapping[int, int],
    games: Mapping[tuple[int, int], GameInfo],
    values: Mapping[int, float],
    slot_counts: Mapping[int, int],
    now: datetime | None,
    leave_empty: bool,
) -> tuple[dict[int, int], AutoFillDay]:
    """One day's best lineup and its summary. `now` set = lock anyone whose game has started."""
    candidates = []
    locked: set[int] = set()
    for pid, player in players.items():
        game = games.get((sp, player.pro_team_id))
        if now is not None and game is not None and game.starts_at <= now:
            locked.add(pid)
        candidates.append(
            AutoFillPlayer(
                id=pid,
                eligible_slots=list(player.eligible_slots or []),
                slot_id=placement.get(pid, BENCH_SLOT),
                has_game=game is not None,
                value=values.get(pid, 0.0),
                injury_status=player.injury_status,
            )
        )
    lineup = best_lineup(candidates, slot_counts, locked, leave_empty)
    starting = {slot for slot in slot_counts if slot not in (BENCH_SLOT, IR_SLOT)}
    playing = [c for c in candidates if c.plays]
    benched = sorted(
        (c for c in playing if lineup[c.id] not in starting and lineup[c.id] != IR_SLOT),
        key=lambda c: (-c.value, c.id),
    )
    return lineup, AutoFillDay(
        sp=sp,
        starters_playing=sum(1 for c in playing if lineup[c.id] in starting),
        slots=len(starting_slots(slot_counts)),
        benched_playing=[players[c.id].full_name for c in benched],
    )


def _response(
    view: TeamDayResponse,
    warnings: list[str],
    days: list[AutoFillDay],
    changed: bool,
) -> AutoFillResponse:
    return AutoFillResponse(
        **view.model_dump(exclude={"warnings"}),
        warnings=warnings + view.warnings,
        days_written=len(days) if changed else 0,
        days=days,
        changed=changed,
    )


def auto_fill_actual(
    db: Session,
    season: int,
    sp: int,
    through_sp: int | None,
    leave_empty: bool,
    basis: Basis,
    now: datetime,
) -> AutoFillResponse:
    """Auto-fill my Actual lineup for day `sp` (through `through_sp`), each day from its own
    games, and store it as a manual edit. Today, anyone whose game has started is locked
    where he is, as ESPN locks him. Commits."""
    last = check_range(season, sp, through_sp)
    team = _require_team(db, season)
    settings_row = league_settings_row(db, season)
    slot_counts = slot_counts_by_id(settings_row.roster_slots if settings_row else None)
    roster = _roster(db, season, team.team_id)
    players = {player.espn_player_id: player for _, player in roster}
    values = player_values(db, season, players)
    days = range(sp, last + 1)
    games = games_index(db, season, days)
    today = today_period(season, now)

    lineups: dict[int, list[tuple[int, int]]] = {}
    summaries: list[AutoFillDay] = []
    changed = False
    for day in days:
        placement, _, _ = actual_placement(db, season, team.team_id, day, roster)
        lineup, summary = _fill_day(
            day,
            players,
            placement,
            games,
            values,
            slot_counts,
            now if day == today else None,
            leave_empty,
        )
        changed = changed or lineup != placement
        lineups[day] = sorted(lineup.items())
        summaries.append(summary)

    # Nothing to change: leave the stored rows (and where they came from) alone.
    warnings = store_lineups(db, season, team, lineups, now) if changed else []
    return _response(team_day(db, season, sp, basis, now), warnings, summaries, changed)


def auto_fill_plan(
    db: Session,
    season: int,
    sp: int,
    through_sp: int | None,
    leave_empty: bool,
    basis: Basis,
    now: datetime,
) -> AutoFillResponse:
    """Auto-fill the Plan view's lineup for day `sp` (through `through_sp`) over each day's
    PLAN roster, and store it as Plan-view rows. Never locks: the plan is hypothetical.
    Never touches the Actual lineup. Commits."""
    last = check_range(season, sp, through_sp)
    team = _require_team(db, season)
    bundle = load_plan(db, season, team, now, through_sp=last)
    days = range(sp, last + 1)
    games = games_index(db, season, days)
    values = player_values(db, season, bundle.players)

    lineups: dict[int, list[tuple[int, int]]] = {}
    summaries: list[AutoFillDay] = []
    changed = False
    for day in days:
        # Placement reads the plan rows as they stand: nothing is written until every day
        # is computed, so each day starts from its own current Plan lineup.
        players, placement, _, _ = _plan_day(db, season, day, bundle)
        lineup, summary = _fill_day(
            day, players, placement, games, values, bundle.slot_counts, None, leave_empty
        )
        changed = changed or lineup != placement
        lineups[day] = sorted(lineup.items())
        summaries.append(summary)

    warnings = store_plan_lineups(db, season, bundle, lineups, now) if changed else []
    view = _plan_day_response(db, season, sp, basis, now, bundle)
    return _response(view, warnings, summaries, changed)
