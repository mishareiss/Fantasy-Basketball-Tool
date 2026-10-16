"""The Team tab's reads and its one write: the day view, lineup edits, the waiver list, the
player card and the calendar.

Endpoints in `app.api.team` are thin wrappers over these. Each view does a fixed handful of
queries however many players it covers: the schedule for every requested day in one query,
every stat line for one basis in one, waiver clears in one batch.
"""

import logging
from collections.abc import Iterable, Sequence
from datetime import datetime

from espn_api.basketball.constant import POSITION_MAP, PRO_TEAM_MAP
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.api.players import _newest_by_player, _per_game
from app.db.models import (
    LINEUP_SOURCE_MANUAL,
    FantasyTeam,
    LineupAssignment,
    NbaGame,
    Player,
    Projection,
    RosterEntry,
)
from app.espn.league import PLACEHOLDER_PRO_TEAM_ID, WAIVERS_STATUS
from app.espn.sync import (
    ACTUAL_CURRENT_KIND,
    ACTUAL_LAST_7_KIND,
    ACTUAL_LAST_15_KIND,
    ACTUAL_LAST_30_KIND,
    ESPN_SOURCE,
    SEASON_PROJECTION_KIND,
)
from app.scoring.settings import AcquisitionSettings
from app.team.calendar import (
    effective_period,
    final_period,
    matchup_for_period,
    matchups,
    period_date,
    period_index,
    today_period,
)
from app.team.display import GameInfo, format_clears_at, format_game
from app.team.grid import (
    DayFilterMode,
    LineupCandidate,
    build_slot_grid,
    passes_day_filter,
    slot_counts_by_id,
    validate_lineup,
)
from app.team.lineup import resolve_lineup
from app.team.schemas import (
    Acquisitions,
    Basis,
    CalendarDay,
    CalendarMatchup,
    CardPlayer,
    CardStats,
    DayGame,
    DayRef,
    FreeAgentRow,
    FreeAgentsResponse,
    GameOut,
    LineupEditResponse,
    MatchupGroup,
    MatchupRef,
    OwnerTeam,
    PlayerCardResponse,
    RosterPlayer,
    ScheduleDay,
    SlotRow,
    StatLine,
    StatSummary,
    TeamCalendarResponse,
    TeamDayResponse,
    TeamRef,
    WaiverOut,
)
from app.team.store import league_settings_row, my_team
from app.team.waivers import WaiverStatus, waiver_statuses

log = logging.getLogger(__name__)

FREEAGENT_STATUS = "FREEAGENT"
POOL_STATUSES = (FREEAGENT_STATUS, WAIVERS_STATUS)

# The columns the tab prints, in order. A response carries the subset some line has.
DISPLAY_STATS = ("MIN", "PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FTM", "DD")

# basis -> the stored actual kind (this season only). "projected" is ESPN's projection.
ACTUAL_BASIS_KINDS: dict[str, str] = {
    "season": ACTUAL_CURRENT_KIND,
    "l7": ACTUAL_LAST_7_KIND,
    "l15": ACTUAL_LAST_15_KIND,
    "l30": ACTUAL_LAST_30_KIND,
}
BASES: tuple[str, ...] = ("projected", *ACTUAL_BASIS_KINDS)

# Positions the waiver list filters on, as ESPN slot ids.
FILTER_POSITIONS = ("PG", "SG", "SF", "PF", "C")


class TeamNotReady(LookupError):
    """No team is marked as ours: the league sync hasn't run, or couldn't tell."""

    def __init__(self, season: int) -> None:
        super().__init__(
            f"No team is marked as ours for season {season}. Run the league sync (make sync) "
            "first, or set ESPN_MY_TEAM_ID."
        )


class PeriodOutOfRange(ValueError):
    """A scoring period outside the fantasy season."""


def check_period(season: int, sp: int, name: str = "sp") -> int:
    last = final_period(season)
    if not 1 <= sp <= last:
        raise PeriodOutOfRange(f"{name} must be between 1 and {last}, not {sp}")
    return sp


# --- building blocks ------------------------------------------------------------------------


def games_index(db: Session, season: int, sps: Iterable[int]) -> dict[tuple[int, int], GameInfo]:
    """`{(sp, pro_team_id): GameInfo}` for every requested day, in one query.

    A team plays at most once a day. If the data ever says otherwise, the earlier game wins
    and the other is logged.
    """
    wanted = sorted(set(sps))
    if not wanted:
        return {}
    out: dict[tuple[int, int], GameInfo] = {}
    for game in db.scalars(
        select(NbaGame)
        .where(NbaGame.season == season, NbaGame.scoring_period_id.in_(wanted))
        .order_by(NbaGame.starts_at, NbaGame.id)
    ):
        sides = (
            (game.home_pro_team_id, game.away_pro_team_id, True),
            (game.away_pro_team_id, game.home_pro_team_id, False),
        )
        for team, opponent, is_home in sides:
            key = (game.scoring_period_id, team)
            if key in out:
                log.warning(
                    "Pro team %s has two games on period %s (%s and %s); keeping the earlier",
                    team,
                    game.scoring_period_id,
                    out[key].game_id,
                    game.id,
                )
                continue
            out[key] = GameInfo(
                game_id=game.id,
                sp=game.scoring_period_id,
                opponent_pro_team_id=opponent,
                opponent=PRO_TEAM_MAP.get(opponent, str(opponent)),
                is_home=is_home,
                starts_at=game.starts_at,
                start_time_tbd=game.start_time_tbd,
            )
    return out


def game_out(info: GameInfo | None) -> GameOut | None:
    if info is None:
        return None
    return GameOut(
        game_id=info.game_id,
        sp=info.sp,
        opponent_pro_team_id=info.opponent_pro_team_id,
        opponent=info.opponent,
        is_home=info.is_home,
        starts_at=info.starts_at,
        start_time_tbd=info.start_time_tbd,
        display=format_game(info),
    )


def _line(row: Projection) -> StatLine:
    return StatLine(
        season=row.season,
        games=row.projected_games,
        fantasy_ppg=row.fantasy_points_per_game,
        fantasy_total=row.fantasy_points_total,
        per_game=_per_game(row),
    )


def stat_lines(
    db: Session, season: int, player_ids: Iterable[int], basis: Basis
) -> dict[int, StatLine]:
    """`{player_id: StatLine}` under one basis, in one query. No row -> not in the dict.

    `projected` is ESPN's season projection, newest season held. The actual bases are this
    season's stored splits and nothing older: last season's line is not "season so far".
    """
    wanted = list(set(player_ids))
    if not wanted:
        return {}
    if basis == "projected":
        rows = _newest_by_player(db, wanted, source=ESPN_SOURCE, kind=SEASON_PROJECTION_KIND)
        return {pid: _line(row) for pid, row in rows.items()}
    kind = ACTUAL_BASIS_KINDS[basis]
    return {
        row.player_id: _line(row)
        for row in db.scalars(
            select(Projection).where(
                Projection.source == ESPN_SOURCE,
                Projection.kind == kind,
                Projection.season == season,
                Projection.player_id.in_(wanted),
            )
        )
    }


def stat_columns(lines: Iterable[StatLine | None]) -> list[str]:
    """The `DISPLAY_STATS` at least one line has, in print order."""
    present: set[str] = set()
    for line in lines:
        if line is not None:
            present.update(line.per_game)
    return [name for name in DISPLAY_STATS if name in present]


def actuals_available(db: Session, season: int) -> bool:
    """Has ESPN published a single actual line for this season yet?"""
    return (
        db.scalar(
            select(Projection.id)
            .where(
                Projection.source == ESPN_SOURCE,
                Projection.kind == ACTUAL_CURRENT_KIND,
                Projection.season == season,
            )
            .limit(1)
        )
        is not None
    )


def waiver_out(status: WaiverStatus | None) -> WaiverOut | None:
    """The wire shape of a waiver status; None for anyone not on waivers."""
    if status is None or not status.on_waivers:
        return None
    return WaiverOut(
        clears_at=status.clears_at,
        clears_at_display=format_clears_at(status.clears_at) if status.clears_at else None,
        clears_at_known=status.clears_at_known,
    )


def matchup_ref(season: int, sp: int) -> MatchupRef:
    matchup = matchup_for_period(season, sp)
    assert matchup is not None  # every period in [1, final] is in a matchup
    return MatchupRef(id=matchup.id, label=matchup.label, start=matchup.start, end=matchup.end)


def matchup_acquisitions_used(totals: dict | None, matchup_id: int) -> int:
    """ESPN's adds-this-matchup count. Its key format is unconfirmed: str or int, absent = 0."""
    totals = totals or {}
    value = totals.get(str(matchup_id), totals.get(matchup_id))
    return int(value) if isinstance(value, int | float) else 0


# --- the day view ---------------------------------------------------------------------------


def _require_team(db: Session, season: int) -> FantasyTeam:
    team = my_team(db, season)
    if team is None:
        raise TeamNotReady(season)
    return team


def _roster(db: Session, season: int, team_id: int) -> list[tuple[RosterEntry, Player]]:
    """My roster with its players, ordered by name (the order a slot's rows fill in)."""
    return list(
        db.execute(
            select(RosterEntry, Player)
            .join(Player, Player.espn_player_id == RosterEntry.player_id)
            .where(RosterEntry.season == season, RosterEntry.team_id == team_id)
            .order_by(Player.full_name, Player.espn_player_id)
        ).tuples()
    )


def _stored_lineup(
    db: Session, season: int, team_id: int, sp: int
) -> tuple[dict[int, dict[int, int]], str]:
    """The stored lineup that governs day `sp` and where it came from. Two queries.

    The governing day is `sp` itself if it has rows, else the nearest earlier stored day.
    """
    base = and_(LineupAssignment.season == season, LineupAssignment.team_id == team_id)
    day = db.scalar(
        select(func.max(LineupAssignment.scoring_period_id)).where(
            base, LineupAssignment.scoring_period_id <= sp
        )
    )
    if day is None:
        return {}, "roster"
    rows = list(
        db.scalars(select(LineupAssignment).where(base, LineupAssignment.scoring_period_id == day))
    )
    if day != sp:
        source = "carried"
    elif any(row.source == LINEUP_SOURCE_MANUAL for row in rows):
        source = "manual"
    else:
        source = "espn"
    return {day: {row.player_id: row.lineup_slot_id for row in rows}}, source


def team_day(db: Session, season: int, sp: int, basis: Basis, now: datetime) -> TeamDayResponse:
    """My roster on day `sp`, in ESPN's slot grid, with each player's game and stat line."""
    check_period(season, sp)
    team = _require_team(db, season)
    settings_row = league_settings_row(db, season)
    rules = AcquisitionSettings.from_raw(
        settings_row.acquisition_settings if settings_row else None
    )
    slot_counts = slot_counts_by_id(settings_row.roster_slots if settings_row else None)

    roster = _roster(db, season, team.team_id)
    stored, source = _stored_lineup(db, season, team.team_id, sp)
    placement = resolve_lineup(
        stored, {entry.player_id: entry.lineup_slot_id for entry, _ in roster}, sp
    )
    players = {player.espn_player_id: player for _, player in roster}

    games = games_index(db, season, [sp])
    lines = stat_lines(db, season, players, basis)
    rows, warnings = build_slot_grid(slot_counts, placement, order=list(players))

    def roster_player(pid: int) -> RosterPlayer:
        player = players[pid]
        return RosterPlayer(
            id=pid,
            name=player.full_name,
            nba_team=player.nba_team,
            positions=list(player.positions or []),
            eligible_slots=list(player.eligible_slots or []),
            injury_status=player.injury_status,
            game=game_out(games.get((sp, player.pro_team_id))),
            stats=lines.get(pid),
        )

    matchup = matchup_ref(season, sp)
    return TeamDayResponse(
        season=season,
        sp=sp,
        date=period_date(season, sp),
        matchup=matchup,
        is_today=today_period(season, now) == sp,
        team=TeamRef(id=team.team_id, name=team.name, abbrev=team.abbrev),
        actuals_available=actuals_available(db, season),
        basis=basis,
        stat_columns=stat_columns(lines.values()),
        lineup_source=source,
        acquisitions=Acquisitions(
            used=matchup_acquisitions_used(team.matchup_acquisition_totals, matchup.id),
            limit=rules.matchup_acquisition_limit,
            matchup_id=matchup.id,
        ),
        slots=[
            SlotRow(
                slot_id=row.slot_id,
                slot=row.slot,
                player=roster_player(row.player_id) if row.player_id is not None else None,
            )
            for row in rows
        ],
        warnings=warnings,
    )


class LineupRejected(ValueError):
    """A manual lineup that breaks the rules. Carries every reason, not just the first."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def edit_lineup(
    db: Session,
    season: int,
    sp: int,
    assignments: Sequence[tuple[int, int]],
    through_sp: int | None,
    basis: Basis,
    now: datetime,
) -> LineupEditResponse:
    """Store a manual lineup for my team on day `sp` (through `through_sp`). Commits.

    Validated as a whole first; any error writes nothing. Replaces those days' rows for my
    team and nobody else's. Only `POST /sync/lineup` ever puts ESPN's lineup back.
    """
    check_period(season, sp)
    last = sp if through_sp is None else check_period(season, through_sp, "through_sp")
    if last < sp:
        raise PeriodOutOfRange(f"through_sp {last} is before sp {sp}")
    team = _require_team(db, season)
    settings_row = league_settings_row(db, season)
    slot_counts = slot_counts_by_id(settings_row.roster_slots if settings_row else None)

    roster = {
        player.espn_player_id: LineupCandidate(
            player_id=player.espn_player_id,
            name=player.full_name,
            eligible_slots=player.eligible_slots,
            injury_status=player.injury_status,
        )
        for _, player in _roster(db, season, team.team_id)
    }
    errors, warnings = validate_lineup(assignments, roster, slot_counts)
    if errors:
        raise LineupRejected(errors)

    db.execute(
        delete(LineupAssignment).where(
            LineupAssignment.season == season,
            LineupAssignment.team_id == team.team_id,
            LineupAssignment.scoring_period_id.between(sp, last),
        )
    )
    for day in range(sp, last + 1):
        for player_id, slot_id in assignments:
            db.add(
                LineupAssignment(
                    season=season,
                    team_id=team.team_id,
                    scoring_period_id=day,
                    player_id=player_id,
                    lineup_slot_id=slot_id,
                    source=LINEUP_SOURCE_MANUAL,
                    updated_at=now,
                )
            )
    db.commit()

    view = team_day(db, season, sp, basis, now)
    return LineupEditResponse(
        **view.model_dump(exclude={"warnings"}),
        warnings=warnings + view.warnings,
        days_written=last - sp + 1,
    )


# --- the waiver list ------------------------------------------------------------------------


def _day_refs(season: int, days: Sequence[int]) -> list[DayRef]:
    refs = []
    for sp in days:
        matchup = matchup_for_period(season, sp)
        refs.append(
            DayRef(sp=sp, date=period_date(season, sp), matchup_id=matchup.id if matchup else None)
        )
    return refs


def _plays(player: Player, filter_slot: int) -> bool:
    return filter_slot in (player.eligible_slots or []) or POSITION_MAP[filter_slot] in (
        player.positions or []
    )


def free_agents(
    db: Session,
    season: int,
    *,
    days: Sequence[int],
    mode: DayFilterMode = "none",
    basis: Basis = "projected",
    position: str | None = None,
    status: str = "all",
    q: str | None = None,
    sort: str = "fpts",
    limit: int = 100,
    offset: int = 0,
    include_unsigned: bool = False,
) -> FreeAgentsResponse:
    """Everyone we could add: FREEAGENT or WAIVERS per ESPN, and on no roster this season.

    The day filter: `any` keeps a player with a game on at least one of `days`, `all` one with
    a game on every one of them, `none` everybody. A fixed handful of queries however big the
    pool: the pool, the schedule, one basis of stat lines, whether actuals exist, and the
    waiver batch for the page's waiver players.
    """
    if not days:
        raise PeriodOutOfRange("days must name at least one scoring period")
    for sp in days:
        check_period(season, sp, "days")

    rostered = select(RosterEntry.player_id).where(RosterEntry.season == season)
    query = select(Player).where(
        Player.roster_status.in_(POOL_STATUSES), Player.espn_player_id.not_in(rostered)
    )
    if status != "all":
        query = query.where(Player.roster_status == status)
    if not include_unsigned:
        query = query.where(
            Player.pro_team_id.is_not(None), Player.pro_team_id != PLACEHOLDER_PRO_TEAM_ID
        )
    pool = list(db.scalars(query))

    if position is not None:
        filter_slot = POSITION_MAP[position]
        pool = [player for player in pool if _plays(player, filter_slot)]
    if q:
        needle = q.casefold()
        pool = [player for player in pool if needle in player.full_name.casefold()]

    games = games_index(db, season, days)

    def schedule(player: Player) -> list[GameInfo | None]:
        return [games.get((sp, player.pro_team_id)) for sp in days]

    by_player = {player.espn_player_id: schedule(player) for player in pool}
    pool = [
        player
        for player in pool
        if passes_day_filter((game is not None for game in by_player[player.espn_player_id]), mode)
    ]

    lines = stat_lines(db, season, (player.espn_player_id for player in pool), basis)

    def games_in_days(player: Player) -> int:
        return sum(game is not None for game in by_player[player.espn_player_id])

    def fpts(player: Player) -> float | None:
        line = lines.get(player.espn_player_id)
        return line.fantasy_ppg if line else None

    def by_fpts(player: Player) -> tuple:
        value = fpts(player)
        return (value is None, -(value or 0.0), player.full_name.casefold())

    if sort == "name":
        pool.sort(key=lambda player: (player.full_name.casefold(), player.espn_player_id))
    elif sort == "games":
        pool.sort(key=lambda player: (-games_in_days(player), *by_fpts(player)))
    else:
        pool.sort(key=by_fpts)

    page = pool[offset : offset + limit]
    waivers = waiver_statuses(
        db,
        season,
        [player.espn_player_id for player in page if player.roster_status == WAIVERS_STATUS],
    )

    rows = []
    for player in page:
        pid = player.espn_player_id
        line = lines.get(pid)
        rows.append(
            FreeAgentRow(
                id=pid,
                name=player.full_name,
                nba_team=player.nba_team,
                positions=list(player.positions or []),
                eligible_slots=list(player.eligible_slots or []),
                injury_status=player.injury_status,
                status=player.roster_status,
                waiver=waiver_out(waivers.get(pid)),
                games=[
                    DayGame(sp=sp, game=game_out(game))
                    for sp, game in zip(days, by_player[pid], strict=True)
                ],
                games_in_days=games_in_days(player),
                stats=StatSummary(fantasy_ppg=line.fantasy_ppg, games=line.games) if line else None,
            )
        )

    return FreeAgentsResponse(
        season=season,
        days=_day_refs(season, days),
        mode=mode,
        basis=basis,
        actuals_available=actuals_available(db, season),
        total=len(pool),
        players=rows,
    )


# --- the player card ------------------------------------------------------------------------

CARD_WINDOW_DAYS = 7


class PlayerNotFound(LookupError):
    pass


def player_card(db: Session, season: int, player_id: int, from_sp: int) -> PlayerCardResponse:
    """One player in full: every stat basis, who holds him, and his next seven days of games,
    grouped by the matchup each day falls in."""
    check_period(season, from_sp, "from_sp")
    player = db.get(Player, player_id)
    if player is None:
        raise PlayerNotFound(f"No player {player_id}")

    holder = db.execute(
        select(RosterEntry.team_id, FantasyTeam.name)
        .outerjoin(
            FantasyTeam,
            and_(
                FantasyTeam.season == RosterEntry.season, FantasyTeam.team_id == RosterEntry.team_id
            ),
        )
        .where(RosterEntry.season == season, RosterEntry.player_id == player_id)
    ).first()
    if holder is not None:
        owner: str | OwnerTeam = OwnerTeam(
            team_id=holder.team_id, name=holder.name or f"Team {holder.team_id}"
        )
    elif player.roster_status == WAIVERS_STATUS:
        owner = WAIVERS_STATUS
    else:
        owner = FREEAGENT_STATUS

    waiver = None
    if player.roster_status == WAIVERS_STATUS and holder is None:
        waiver = waiver_out(waiver_statuses(db, season, [player_id]).get(player_id))

    lines = {basis: stat_lines(db, season, [player_id], basis).get(player_id) for basis in BASES}

    last = min(from_sp + CARD_WINDOW_DAYS - 1, final_period(season))
    window = list(range(from_sp, last + 1))
    games = games_index(db, season, window)
    schedule: list[ScheduleDay] = []
    groups: dict[int, MatchupGroup] = {}
    for sp in window:
        day = period_date(season, sp)
        matchup = matchup_for_period(season, sp)
        game = game_out(games.get((sp, player.pro_team_id)))
        schedule.append(
            ScheduleDay(
                sp=sp,
                date=day,
                weekday=f"{day:%a}",
                matchup_id=matchup.id if matchup else None,
                matchup_label=matchup.label if matchup else None,
                game=game,
            )
        )
        if matchup is None:
            continue
        group = groups.setdefault(
            matchup.id,
            MatchupGroup(
                matchup_id=matchup.id,
                label=matchup.label,
                start=matchup.start,
                end=matchup.end,
                days=[],
                games=0,
            ),
        )
        group.days.append(sp)
        group.games += game is not None

    return PlayerCardResponse(
        season=season,
        player=CardPlayer(
            id=player.espn_player_id,
            name=player.full_name,
            nba_team=player.nba_team,
            positions=list(player.positions or []),
            eligible_slots=list(player.eligible_slots or []),
            injury_status=player.injury_status,
            owner=owner,
        ),
        waiver=waiver,
        actuals_available=actuals_available(db, season),
        stat_columns=stat_columns(lines.values()),
        stats=CardStats(**lines),
        schedule=schedule,
        by_matchup=list(groups.values()),
    )


# --- the calendar ---------------------------------------------------------------------------


def team_calendar(db: Session, season: int, now: datetime) -> TeamCalendarResponse:
    """Every fantasy day with its matchup and how many of my players play, in one grouped query."""
    last = final_period(season)
    team = my_team(db, season)
    my_games: dict[int, int] = {}
    if team is not None:
        plays = or_(
            NbaGame.home_pro_team_id == Player.pro_team_id,
            NbaGame.away_pro_team_id == Player.pro_team_id,
        )
        my_games = dict(
            db.execute(
                select(NbaGame.scoring_period_id, func.count())
                .select_from(RosterEntry)
                .join(Player, Player.espn_player_id == RosterEntry.player_id)
                .join(NbaGame, and_(NbaGame.season == season, plays))
                .where(
                    RosterEntry.season == season,
                    RosterEntry.team_id == team.team_id,
                    NbaGame.scoring_period_id.between(1, last),
                )
                .group_by(NbaGame.scoring_period_id)
            )
            .tuples()
            .all()
        )

    days = []
    for sp in range(1, last + 1):
        matchup = matchup_for_period(season, sp)
        days.append(
            CalendarDay(
                sp=sp,
                date=period_date(season, sp),
                matchup_id=matchup.id if matchup else None,
                my_games=my_games.get(sp, 0),
            )
        )
    return TeamCalendarResponse(
        season=season,
        today_period=today_period(season, now),
        effective_period=effective_period(season, now),
        final_period=last,
        matchups=[
            CalendarMatchup(
                id=m.id,
                label=m.label,
                start=m.start,
                end=m.end,
                first_sp=period_index(season, m.start),
                last_sp=period_index(season, m.end),
            )
            for m in matchups(season)
        ],
        days=days,
    )
