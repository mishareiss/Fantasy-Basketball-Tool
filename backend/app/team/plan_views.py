"""The waiver plan's reads and writes: the plan, the Plan view of a day, plan lineup edits,
move CRUD and acquisition overrides.

The rules live in the pure `app.team.plan`. This module loads rows into it and turns what
comes back into wire shapes. Endpoints in `app.api.team` are thin wrappers over these.

Validation here is data shape only (a day in the season, at least one candidate, exactly one
drop reference, a referenced move that exists and is ours, a resolved player who is one of
the candidates). Every league rule is a warning from the engine, never a refusal.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    PLAN_PLANNED,
    AcquisitionOverride,
    FantasyTeam,
    PlanCandidate,
    PlanLineupAssignment,
    PlanMove,
    Player,
    RosterEntry,
)
from app.scoring.settings import AcquisitionSettings
from app.team.calendar import (
    effective_period,
    final_period,
    matchup_by_id,
    matchups,
    period_date,
)
from app.team.grid import LineupCandidate, slot_counts_by_id, validate_lineup
from app.team.plan import (
    AcquisitionTally,
    Move,
    PlanContext,
    PlanWarning,
    Simulation,
    acquisition_tallies,
    effective_pickup,
    landing_period,
    pickup_alternatives,
    plan_lineup,
    plan_matchup_ids,
    resolve_drop,
    simulate,
    warnings,
)
from app.team.plan_detect import pending_claims
from app.team.plan_store import engine_move, load_moves
from app.team.schemas import (
    AcquisitionRow,
    Acquisitions,
    Basis,
    LineupEditResponse,
    PlanCandidateOut,
    PlanDeleteResponse,
    PlanDropOut,
    PlanFlag,
    PlanMoveOut,
    PlanMutationResponse,
    PlannedDropOut,
    PlanPlayerRef,
    PlanResponse,
    PlanWarningOut,
    TeamDayResponse,
)
from app.team.store import league_settings_row
from app.team.views import (
    LineupRejected,
    _require_team,
    _roster,
    actual_placement,
    check_period,
    check_range,
    day_response,
    games_index,
    matchup_acquisitions_used,
    matchup_ref,
)
from app.team.waivers import waiver_statuses


class PlanInvalid(ValueError):
    """A move whose data doesn't hang together. Carries every reason, not just the first."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class MoveNotFound(LookupError):
    pass


class MoveHasDependents(Exception):
    """A delete that would orphan chained drops. The caller can repoint them first."""

    def __init__(self, move_id: int, dependents: list[int]) -> None:
        super().__init__(
            f"Move {move_id} is the drop of move(s) {', '.join(map(str, dependents))}; repoint "
            "them first, or delete with ?repoint=true to turn each into a named drop"
        )
        self.move_id = move_id
        self.dependents = dependents


# --- loading the plan -----------------------------------------------------------------------


@dataclass(frozen=True)
class PlanBundle:
    """One team's plan, loaded and run through the engine."""

    team: FantasyTeam
    today_sp: int
    roster: list[tuple[RosterEntry, Player]]
    rows: list[PlanMove]
    moves: list[Move]
    sim: Simulation
    ctx: PlanContext
    warnings: list[PlanWarning]
    players: dict[int, Player]
    pending: set[int]
    slot_counts: dict[int, int]


def _referenced_ids(rows: Iterable[PlanMove]) -> set[int]:
    ids: set[int] = set()
    for row in rows:
        ids.update(c.player_id for c in row.candidates)
        for pid in (row.drop_player_id, row.resolved_player_id, row.detected_drop_player_id):
            if pid is not None:
                ids.add(pid)
    return ids


def load_plan(
    db: Session, season: int, team: FantasyTeam, now: datetime, through_sp: int | None = None
) -> PlanBundle:
    """Load my plan and everything its rules need, simulate it through the last move's day
    (or `through_sp`, if later), and collect its warnings. A fixed handful of queries."""
    today = effective_period(season, now)
    settings_row = league_settings_row(db, season)
    rules = AcquisitionSettings.from_raw(
        settings_row.acquisition_settings if settings_row else None
    )
    roster = _roster(db, season, team.team_id)
    rows = load_moves(db, season, team.team_id)
    moves = [engine_move(row) for row in rows]
    through = max([today, through_sp or today, *(m.sp for m in moves)])
    through = min(through, final_period(season))
    sim = simulate([entry.player_id for entry, _ in roster], moves, today, through)

    players = {player.espn_player_id: player for _, player in roster}
    wanted = _referenced_ids(rows) - set(players)
    if wanted:
        players.update(
            (p.espn_player_id, p)
            for p in db.scalars(select(Player).where(Player.espn_player_id.in_(wanted)))
        )

    candidates = {c.player_id for row in rows for c in row.candidates}
    on_waivers = {
        pid: (status.clears_at if status.clears_at_known else None)
        for pid, status in waiver_statuses(db, season, candidates).items()
        if status.on_waivers
    }

    planned = [m for m in moves if m.status == PLAN_PLANNED]
    landing = {landing_period(m, today) for m in planned}
    schedule = games_index(db, season, landing)
    games = {
        (sp, pid)
        for m in planned
        for sp in [landing_period(m, today)]
        for pid in m.candidates
        if pid in players and (sp, players[pid].pro_team_id) in schedule
    }

    overrides = {
        row.matchup_id: row.used
        for row in db.scalars(
            select(AcquisitionOverride).where(
                AcquisitionOverride.season == season,
                AcquisitionOverride.team_id == team.team_id,
            )
        )
    }
    ctx = PlanContext(
        season=season,
        today_sp=today,
        names={pid: p.full_name for pid, p in players.items()},
        acquired_at={entry.player_id: entry.acquisition_date for entry, _ in roster},
        on_waivers=on_waivers,
        waiver_hours=rules.waiver_hours,
        waiver_tz=get_settings().waiver_clear_tz,
        move_time=get_settings().plan_move_time(),
        acquisition_limit=rules.matchup_acquisition_limit,
        # Every matchup, not just the plan's: the day view asks about whichever day it shows.
        espn_used={
            m.id: matchup_acquisitions_used(team.matchup_acquisition_totals, m.id)
            for m in matchups(season)
        },
        used_overrides=overrides,
        games=games,
    )
    return PlanBundle(
        team=team,
        today_sp=today,
        roster=roster,
        rows=rows,
        moves=moves,
        sim=sim,
        ctx=ctx,
        warnings=warnings(moves, sim, ctx),
        players=players,
        pending=pending_claims(db, season, team.team_id, rows),
        slot_counts=slot_counts_by_id(settings_row.roster_slots if settings_row else None),
    )


# --- wire shapes ----------------------------------------------------------------------------


def _ref(players: Mapping[int, Player], pid: int | None) -> PlanPlayerRef | None:
    if pid is None:
        return None
    player = players.get(pid)
    if player is None:
        return PlanPlayerRef(id=pid, name=f"player {pid}")
    return PlanPlayerRef(
        id=pid,
        name=player.full_name,
        nba_team=player.nba_team,
        positions=list(player.positions or []),
        injury_status=player.injury_status,
    )


def _refs(players: Mapping[int, Player], pids: Iterable[int]) -> list[PlanPlayerRef]:
    return [ref for pid in pids if (ref := _ref(players, pid)) is not None]


def warning_out(warning: PlanWarning) -> PlanWarningOut:
    return PlanWarningOut(
        code=warning.code,
        severity=warning.severity,
        message=warning.message,
        move_id=warning.move_id,
        matchup_id=warning.matchup_id,
        sp=warning.sp,
        detail=dict(warning.detail),
    )


def _acquisition_row(tally: AcquisitionTally) -> AcquisitionRow:
    return AcquisitionRow(
        matchup_id=tally.matchup_id,
        label=tally.label,
        used=tally.used,
        used_source=tally.used_source,
        planned=tally.planned,
        limit=tally.limit,
        over=tally.over,
    )


def drop_display(player: PlanPlayerRef | None, alternatives: Sequence[PlanPlayerRef]) -> str:
    """`A`, or `A (or B)` / `A (or B or C)` for a chained drop still unsettled."""
    if player is None:
        return "nobody"
    if not alternatives:
        return player.name
    return f"{player.name} (or {' or '.join(alt.name for alt in alternatives)})"


def plan_response(db: Session, season: int, now: datetime) -> PlanResponse:
    """Every move with its candidates, resolved drop and status; every warning; and the
    acquisition math for the current matchup through the last one any move touches."""
    team = _require_team(db, season)
    bundle = load_plan(db, season, team, now)
    return _plan_response(season, bundle)


def _plan_response(season: int, bundle: PlanBundle) -> PlanResponse:
    by_id = {move.id: move for move in bundle.moves}
    players = bundle.players
    out = []
    for row, move in zip(bundle.rows, bundle.moves, strict=True):
        drop = resolve_drop(move, by_id)
        drop_player = _ref(players, drop.player_id)
        drop_alts = _refs(players, drop.alternatives)
        planned = move.status == PLAN_PLANNED
        matchup = matchup_ref(season, move.sp)
        out.append(
            PlanMoveOut(
                id=move.id,
                sp=move.sp,
                date=period_date(season, move.sp),
                matchup_id=matchup.id,
                sequence=move.sequence,
                status=move.status,
                lands_sp=landing_period(move, bundle.today_sp) if planned else None,
                overdue=planned and move.sp < bundle.today_sp,
                drop=PlanDropOut(
                    player=drop_player,
                    alternatives=drop_alts,
                    from_move_id=move.drop_from_move_id,
                    problem=drop.problem,
                    display=drop_display(drop_player, drop_alts),
                ),
                candidates=[
                    PlanCandidateOut(rank=rank, player=_ref(players, pid))
                    for rank, pid in enumerate(move.candidates, start=1)
                ],
                pickup=_ref(players, effective_pickup(move)),
                alternatives=_refs(players, pickup_alternatives(move)),
                resolved_player_id=move.resolved_player_id,
                claim_pending=move.id in bundle.pending,
                espn_transaction_id=row.espn_transaction_id,
                note=row.note,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    tallies = acquisition_tallies(
        bundle.moves, bundle.ctx, plan_matchup_ids(bundle.moves, season, bundle.today_sp)
    )
    return PlanResponse(
        season=season,
        today_sp=bundle.today_sp,
        moves=out,
        warnings=[warning_out(w) for w in bundle.warnings],
        acquisitions=[_acquisition_row(t) for t in tallies],
    )


# --- the Plan view of a day -----------------------------------------------------------------


def _plan_rows(db: Session, season: int, team_id: int, sp: int) -> dict[int, dict[int, int]]:
    """The Plan-view rows governing day `sp`: that day's, else the nearest earlier edited
    day's. Two queries, like the Actual view's `_stored_lineup`."""
    base = and_(PlanLineupAssignment.season == season, PlanLineupAssignment.team_id == team_id)
    day = db.scalar(
        select(func.max(PlanLineupAssignment.scoring_period_id)).where(
            base, PlanLineupAssignment.scoring_period_id <= sp
        )
    )
    if day is None:
        return {}
    rows = db.scalars(
        select(PlanLineupAssignment).where(base, PlanLineupAssignment.scoring_period_id == day)
    )
    return {day: {row.player_id: row.lineup_slot_id for row in rows}}


def _plan_day(
    db: Session, season: int, sp: int, bundle: PlanBundle
) -> tuple[dict[int, Player], dict[int, int], str, int | None]:
    """The Plan view's players (slot-fill order) and placement on day `sp`, with its source."""
    day = bundle.sim.day(sp)
    team_id = bundle.team.team_id
    actual, _, _ = actual_placement(db, season, team_id, sp, bundle.roster)
    eligible = {pid: list(p.eligible_slots or []) for pid, p in bundle.players.items()}
    lineup = plan_lineup(day, actual, _plan_rows(db, season, team_id, sp), eligible)
    players = {pid: bundle.players[pid] for pid in day.roster if pid in bundle.players}
    ordered = dict(
        sorted(players.items(), key=lambda item: (item[1].full_name, item[1].espn_player_id))
    )
    placement = {pid: slot for pid, slot in lineup.placement.items() if pid in ordered}
    return ordered, placement, lineup.source, lineup.carried_from_sp


def team_day_plan(
    db: Session, season: int, sp: int, basis: Basis, now: datetime
) -> TeamDayResponse:
    """My roster on day `sp` as if the plan had been carried out, in the same slot grid as
    the Actual view, each player flagged actual or planned."""
    check_period(season, sp)
    team = _require_team(db, season)
    bundle = load_plan(db, season, team, now, through_sp=sp)
    return _plan_day_response(db, season, sp, basis, now, bundle)


def _plan_day_response(
    db: Session, season: int, sp: int, basis: Basis, now: datetime, bundle: PlanBundle
) -> TeamDayResponse:
    day = bundle.sim.day(sp)
    players, placement, source, carried_from = _plan_day(db, season, sp, bundle)
    flags = {
        pid: PlanFlag(
            status=entry.status,
            move_id=entry.move_id,
            alternatives=_refs(bundle.players, entry.alternatives),
            claim_pending=entry.move_id in bundle.pending if entry.move_id else False,
        )
        for pid, entry in day.roster.items()
    }
    matchup = matchup_ref(season, sp)
    (tally,) = acquisition_tallies(bundle.moves, bundle.ctx, [matchup.id])
    day_warnings = [
        warning_out(w)
        for w in bundle.warnings
        if w.sp == sp or (w.sp is None and w.matchup_id == matchup.id)
    ]
    return day_response(
        db,
        season,
        sp,
        basis,
        now,
        team=bundle.team,
        slot_counts=bundle.slot_counts,
        players=players,
        placement=placement,
        lineup_source=source,
        carried_from_sp=carried_from,
        acquisitions=Acquisitions(
            used=tally.used,
            limit=tally.limit,
            matchup_id=matchup.id,
            used_source=tally.used_source,
            planned=tally.planned,
        ),
        plan_flags=flags,
        view="plan",
        planned_drops=[
            PlannedDropOut(
                player=_ref(bundle.players, drop.player_id),
                move_id=drop.move_id,
                alternatives=_refs(bundle.players, drop.alternatives),
            )
            for drop in day.planned_drops
        ],
        plan_warnings=day_warnings,
    )


def edit_plan_lineup(
    db: Session,
    season: int,
    sp: int,
    assignments: Sequence[tuple[int, int]],
    through_sp: int | None,
    basis: Basis,
    now: datetime,
) -> LineupEditResponse:
    """Store a Plan-view lineup for day `sp` (through `through_sp`). Commits.

    The same rules as an Actual edit (`validate_lineup`), checked against the PLAN roster of
    every day written, so a range that crosses a planned move is refused rather than written
    with a player who isn't there. Writes `PlanLineupAssignment` only: the Actual lineup is
    never touched.
    """
    last = check_range(season, sp, through_sp)
    team = _require_team(db, season)
    bundle = load_plan(db, season, team, now, through_sp=last)
    warnings_out = store_plan_lineups(
        db, season, bundle, {day_sp: assignments for day_sp in range(sp, last + 1)}, now
    )
    view = _plan_day_response(db, season, sp, basis, now, bundle)
    return LineupEditResponse(
        **view.model_dump(exclude={"warnings"}),
        warnings=warnings_out + view.warnings,
        days_written=last - sp + 1,
    )


def store_plan_lineups(
    db: Session,
    season: int,
    bundle: PlanBundle,
    lineups: Mapping[int, Sequence[tuple[int, int]]],
    now: datetime,
) -> list[str]:
    """Store Plan-view lineups, `{sp: [(player_id, slot_id)]}`. Commits.

    Each day is checked against that day's PLAN roster (`bundle` must be simulated through
    the last day); any error on any day writes nothing. Returns the first day's warnings.
    """
    errors: list[str] = []
    warnings_out: list[str] = []
    first = min(lineups)
    for day_sp, assignments in sorted(lineups.items()):
        roster = {
            pid: LineupCandidate(
                player_id=pid,
                name=bundle.players[pid].full_name,
                eligible_slots=bundle.players[pid].eligible_slots,
                injury_status=bundle.players[pid].injury_status,
            )
            for pid in bundle.sim.day(day_sp).roster
            if pid in bundle.players
        }
        day_errors, day_warnings = validate_lineup(assignments, roster, bundle.slot_counts)
        prefix = "" if len(lineups) == 1 else f"Day {day_sp}: "
        errors.extend(prefix + e for e in day_errors)
        if day_sp == first:
            warnings_out.extend(day_warnings)
    if errors:
        raise LineupRejected(errors)

    db.execute(
        delete(PlanLineupAssignment).where(
            PlanLineupAssignment.season == season,
            PlanLineupAssignment.team_id == bundle.team.team_id,
            PlanLineupAssignment.scoring_period_id.in_(list(lineups)),
        )
    )
    for day_sp, assignments in lineups.items():
        for player_id, slot_id in assignments:
            db.add(
                PlanLineupAssignment(
                    season=season,
                    team_id=bundle.team.team_id,
                    scoring_period_id=day_sp,
                    player_id=player_id,
                    lineup_slot_id=slot_id,
                    updated_at=now,
                )
            )
    db.commit()
    return warnings_out


# --- writes ---------------------------------------------------------------------------------


def _get_move(db: Session, season: int, team_id: int, move_id: int) -> PlanMove:
    row = db.get(PlanMove, move_id)
    if row is None or row.season != season or row.team_id != team_id:
        raise MoveNotFound(f"No move {move_id} in my plan")
    return row


def _validate(
    db: Session,
    season: int,
    team_id: int,
    *,
    sp: int,
    drop_player_id: int | None,
    drop_from_move_id: int | None,
    candidate_ids: Sequence[int],
    resolved_player_id: int | None,
) -> None:
    """The shape rules. Raises `PlanInvalid` with every problem found."""
    check_period(season, sp)
    errors: list[str] = []
    if not candidate_ids:
        errors.append("candidate_ids needs at least one player")
    duplicates = sorted({pid for pid in candidate_ids if list(candidate_ids).count(pid) > 1})
    if duplicates:
        errors.append(f"candidate_ids lists {', '.join(map(str, duplicates))} more than once")
    if (drop_player_id is None) == (drop_from_move_id is None):
        errors.append("give exactly one of drop_player_id and drop_from_move_id")
    if drop_from_move_id is not None:
        target = db.get(PlanMove, drop_from_move_id)
        if target is None or target.season != season or target.team_id != team_id:
            errors.append(f"drop_from_move_id {drop_from_move_id} is not a move in my plan")
    if resolved_player_id is not None and resolved_player_id not in candidate_ids:
        errors.append(f"resolved_player_id {resolved_player_id} is not one of the candidates")

    wanted = set(candidate_ids) | ({drop_player_id} if drop_player_id is not None else set())
    if wanted:
        known = set(
            db.scalars(select(Player.espn_player_id).where(Player.espn_player_id.in_(wanted)))
        )
        unknown = sorted(wanted - known)
        if unknown:
            errors.append(f"unknown player id(s): {', '.join(map(str, unknown))}")
    if errors:
        raise PlanInvalid(errors)


def _last_sequence(db: Session, season: int, team_id: int, sp: int, exclude: int | None) -> int:
    query = select(func.max(PlanMove.sequence)).where(
        PlanMove.season == season,
        PlanMove.team_id == team_id,
        PlanMove.scoring_period_id == sp,
    )
    if exclude is not None:
        query = query.where(PlanMove.id != exclude)
    current = db.scalar(query)
    return 0 if current is None else current + 1


def _mutation(db: Session, season: int, team: FantasyTeam, now: datetime, move_id: int | None):
    bundle = load_plan(db, season, team, now)
    return PlanMutationResponse(**_plan_response(season, bundle).model_dump(), move_id=move_id)


def create_move(
    db: Session,
    season: int,
    now: datetime,
    *,
    sp: int,
    drop_player_id: int | None,
    drop_from_move_id: int | None,
    candidate_ids: Sequence[int],
    note: str | None,
    sequence: int | None,
) -> PlanMutationResponse:
    """Add a move to my plan. Commits. Returns the whole plan, since warnings move with it."""
    team = _require_team(db, season)
    _validate(
        db,
        season,
        team.team_id,
        sp=sp,
        drop_player_id=drop_player_id,
        drop_from_move_id=drop_from_move_id,
        candidate_ids=candidate_ids,
        resolved_player_id=None,
    )
    row = PlanMove(
        season=season,
        team_id=team.team_id,
        scoring_period_id=sp,
        sequence=(
            sequence if sequence is not None else _last_sequence(db, season, team.team_id, sp, None)
        ),
        drop_player_id=drop_player_id,
        drop_from_move_id=drop_from_move_id,
        status=PLAN_PLANNED,
        drop_mismatch=False,
        note=note,
        created_at=now,
        updated_at=now,
    )
    row.candidates = [
        PlanCandidate(rank=rank, player_id=pid) for rank, pid in enumerate(candidate_ids, start=1)
    ]
    db.add(row)
    db.commit()
    return _mutation(db, season, team, now, row.id)


def update_move(
    db: Session, season: int, move_id: int, fields: Mapping[str, object], now: datetime
) -> PlanMutationResponse:
    """Change any of a move's fields (`fields` holds only what was sent). Commits.

    Sending one drop reference clears the other. Any edit acknowledges an auto-detect
    "different drop" info. Setting a move back to `planned` forgets its ESPN transaction.
    """
    team = _require_team(db, season)
    row = _get_move(db, season, team.team_id, move_id)

    drop_player_id = row.drop_player_id
    drop_from_move_id = row.drop_from_move_id
    if "drop_player_id" in fields or "drop_from_move_id" in fields:
        drop_player_id = fields.get("drop_player_id", drop_player_id)  # type: ignore[assignment]
        drop_from_move_id = fields.get("drop_from_move_id", drop_from_move_id)  # type: ignore[assignment]
        if fields.get("drop_player_id") is not None and "drop_from_move_id" not in fields:
            drop_from_move_id = None
        if fields.get("drop_from_move_id") is not None and "drop_player_id" not in fields:
            drop_player_id = None

    sp = fields.get("sp", row.scoring_period_id)
    if sp is None:
        raise PlanInvalid(["sp can't be null"])
    candidate_ids = fields.get("candidate_ids")
    if "candidate_ids" in fields and candidate_ids is None:
        raise PlanInvalid(["candidate_ids can't be null"])
    candidates = (
        list(candidate_ids)  # type: ignore[arg-type]
        if candidate_ids is not None
        else [c.player_id for c in row.candidates]
    )
    resolved = fields.get("resolved_player_id", row.resolved_player_id)
    _validate(
        db,
        season,
        team.team_id,
        sp=sp,  # type: ignore[arg-type]
        drop_player_id=drop_player_id,
        drop_from_move_id=drop_from_move_id,
        candidate_ids=candidates,
        resolved_player_id=resolved,  # type: ignore[arg-type]
    )
    if "status" in fields and fields["status"] is None:
        raise PlanInvalid(["status can't be null"])

    moved_day = sp != row.scoring_period_id
    row.scoring_period_id = sp  # type: ignore[assignment]
    if "sequence" in fields and fields["sequence"] is not None:
        row.sequence = fields["sequence"]  # type: ignore[assignment]
    elif moved_day:
        row.sequence = _last_sequence(db, season, team.team_id, sp, row.id)  # type: ignore[arg-type]
    row.drop_player_id = drop_player_id
    row.drop_from_move_id = drop_from_move_id
    if candidate_ids is not None:
        row.candidates.clear()
        db.flush()
        row.candidates.extend(
            PlanCandidate(rank=rank, player_id=pid) for rank, pid in enumerate(candidates, start=1)
        )
    row.resolved_player_id = resolved  # type: ignore[assignment]
    if "status" in fields:
        row.status = fields["status"]  # type: ignore[assignment]
        if row.status == PLAN_PLANNED:
            row.espn_transaction_id = None
    if "note" in fields:
        row.note = fields["note"]  # type: ignore[assignment]
    row.drop_mismatch = False
    row.detected_drop_player_id = None
    row.updated_at = now
    db.commit()
    return _mutation(db, season, team, now, row.id)


def delete_move(
    db: Session, season: int, move_id: int, now: datetime, *, repoint: bool
) -> PlanDeleteResponse:
    """Delete a move. If another move's drop is "this move's pickup", refuse (with their
    ids) unless `repoint`, which first turns each into a named drop of this move's
    effective pickup. Commits."""
    team = _require_team(db, season)
    row = _get_move(db, season, team.team_id, move_id)
    dependents = list(
        db.scalars(
            select(PlanMove).where(PlanMove.drop_from_move_id == move_id).order_by(PlanMove.id)
        )
    )
    if dependents and not repoint:
        raise MoveHasDependents(move_id, [d.id for d in dependents])

    pickup = effective_pickup(engine_move(row))
    for dependent in dependents:
        dependent.drop_player_id = pickup
        dependent.drop_from_move_id = None
        dependent.updated_at = now
    db.flush()
    db.delete(row)
    db.commit()

    bundle = load_plan(db, season, team, now)
    return PlanDeleteResponse(
        **_plan_response(season, bundle).model_dump(),
        deleted_move_id=move_id,
        repointed=[d.id for d in dependents],
    )


def set_acquisition_override(
    db: Session, season: int, matchup_id: int, used: int | None, now: datetime
) -> PlanMutationResponse:
    """Set (or, with None, clear) the hand-set adds-used count for one matchup. Commits."""
    team = _require_team(db, season)
    try:
        matchup_by_id(season, matchup_id)
    except ValueError as exc:
        raise PlanInvalid([str(exc)]) from exc
    row = db.get(AcquisitionOverride, (season, team.team_id, matchup_id))
    if used is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(
            AcquisitionOverride(
                season=season,
                team_id=team.team_id,
                matchup_id=matchup_id,
                used=used,
                updated_at=now,
            )
        )
    else:
        row.used = used
        row.updated_at = now
    db.commit()
    return _mutation(db, season, team, now, None)
