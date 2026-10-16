"""The waiver plan's rules engine. Pure: no database, no ESPN, no clock.

Dataclasses in, dataclasses out, so every rule can be tested with a handful of integers. The
database side (`app.team.plan_views`) loads rows into these and turns the results into wire
shapes; auto-detect (`app.team.plan_detect`) marks moves done from ESPN's log.

The model, in Misha's words: a move is "on day D, drop X, pick up Y", with ordered backups
for Y. A later move may drop "whoever move N picked up" instead of a named player, and that
drop follows move N wherever it resolves. Rules WARN, never block: nothing in here refuses
anything, it only says what looks wrong.

How the plan plays out (`simulate`): start from the roster ESPN gave us, then apply every
`planned` move in `(sp, sequence, id)` order. A `done` move is already in that roster, so it is
never applied again; a `skipped` one is ignored. A move dated before today lands today: it is
overdue, not gone. A drop that can't happen (he isn't on the plan roster) still lets the
pickup through, with a warning, so a mistake is visible instead of silently swallowing a move.
"""

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal

from app.team.calendar import PACIFIC, matchup_by_id, matchup_for_period, period_date
from app.team.lineup import BENCH_SLOT, IR_SLOT, resolve_lineup
from app.team.waiver_rule import waiver_clears_at

# Mirrors `app.db.models.plan.PLAN_*` (this module can't import the models; a test asserts the
# two agree).
PLANNED = "planned"
DONE = "done"
SKIPPED = "skipped"

Severity = Literal["warning", "info"]
ChainProblem = Literal["missing", "later", "skipped", "cycle"]
PlanStatus = Literal["actual", "planned"]

# Stable warning codes. The UI keys on these; the messages are for people.
ACQ_LIMIT = "acq_limit"
ON_WAIVERS = "on_waivers"
DROP_NOT_ROSTERED = "drop_not_rostered"
PICKUP_ALREADY_ROSTERED = "pickup_already_rostered"
DOUBLE_PICKUP = "double_pickup"
BAD_CHAIN = "bad_chain"
CHAIN_CYCLE = "chain_cycle"
BACKUP_OVERLAP = "backup_overlap"
NO_GAME = "no_game"
OVERDUE = "overdue"
DIFFERENT_DROP = "different_drop"


# --- moves and how they resolve -------------------------------------------------------------


@dataclass(frozen=True)
class Move:
    """One planned move. `candidates` are player ids in rank order (primary first)."""

    id: int
    sp: int
    candidates: tuple[int, ...]
    sequence: int = 0
    drop_player_id: int | None = None
    drop_from_move_id: int | None = None
    status: str = PLANNED
    resolved_player_id: int | None = None
    # Auto-detect saw the ESPN move drop someone else (`detected_drop_player_id`, None = nobody).
    drop_mismatch: bool = False
    detected_drop_player_id: int | None = None

    @property
    def order_key(self) -> tuple[int, int, int]:
        return (self.sp, self.sequence, self.id)


def effective_pickup(move: Move) -> int | None:
    """Who the move picks up: the resolved candidate if there is one, else the primary."""
    if move.resolved_player_id is not None:
        return move.resolved_player_id
    return move.candidates[0] if move.candidates else None


def pickup_alternatives(move: Move) -> tuple[int, ...]:
    """The backups still in play, in rank order. Empty once the move is resolved."""
    if move.resolved_player_id is not None:
        return ()
    return tuple(move.candidates[1:])


@dataclass(frozen=True)
class ResolvedDrop:
    """Who a move drops. For a chained drop, `alternatives` are the referenced move's backups,
    so the UI can say "A (or B)". `problem` is set when the chain can't be trusted."""

    player_id: int | None
    alternatives: tuple[int, ...] = ()
    from_move_id: int | None = None
    problem: ChainProblem | None = None


def resolve_drop(move: Move, moves_by_id: Mapping[int, Move]) -> ResolvedDrop:
    """A named drop as is; a chained one as the referenced move's effective pickup.

    The chain is walked to its end to catch a loop that comes back to this move (A drops B's
    pickup, B drops A's): that is a `cycle`. The walk keeps a visited set, so a loop further
    down that doesn't include this move just stops it. It never spins.
    """
    if move.drop_from_move_id is None:
        return ResolvedDrop(move.drop_player_id)

    seen = {move.id}
    current = move
    while current.drop_from_move_id is not None:
        if current.drop_from_move_id == move.id:
            return ResolvedDrop(None, (), move.drop_from_move_id, "cycle")
        following = moves_by_id.get(current.drop_from_move_id)
        if following is None or following.id in seen:
            break
        seen.add(following.id)
        current = following

    target = moves_by_id.get(move.drop_from_move_id)
    if target is None:
        return ResolvedDrop(None, (), move.drop_from_move_id, "missing")
    resolved = ResolvedDrop(effective_pickup(target), pickup_alternatives(target), target.id)
    if target.status == SKIPPED:
        return ResolvedDrop(resolved.player_id, resolved.alternatives, target.id, "skipped")
    if target.status == PLANNED and target.order_key > move.order_key:
        return ResolvedDrop(resolved.player_id, resolved.alternatives, target.id, "later")
    return resolved


def landing_period(move: Move, today_sp: int) -> int:
    """The day a planned move takes effect in the plan: its own day, or today if overdue."""
    return max(move.sp, today_sp)


# --- the simulation -------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanEntry:
    """One player on the plan roster: already mine (`actual`) or a planned pickup."""

    player_id: int
    status: PlanStatus
    move_id: int | None = None
    alternatives: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlannedDrop:
    player_id: int
    move_id: int
    alternatives: tuple[int, ...] = ()


@dataclass(frozen=True)
class AppliedMove:
    """What one planned move did. `added` / `dropped` are None when that half couldn't happen."""

    move_id: int
    sp: int
    added: int | None
    dropped: int | None


@dataclass(frozen=True)
class Conflict:
    """A roster-shape problem found while applying a move (turned into words by `warnings`)."""

    code: str
    move_id: int
    sp: int
    player_id: int | None = None
    other_move_id: int | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PlanDay:
    sp: int
    # Ordered: the actual roster in the order given, then pickups in the order they land.
    roster: Mapping[int, PlanEntry]
    # Dropped by moves landing on this day.
    planned_drops: tuple[PlannedDrop, ...] = ()
    # Every move that has landed on or before this day, in order.
    applied: tuple[AppliedMove, ...] = ()


@dataclass(frozen=True)
class Simulation:
    today_sp: int
    through_sp: int
    base: Mapping[int, PlanEntry]
    days: Mapping[int, PlanDay]
    applied: tuple[AppliedMove, ...]
    conflicts: tuple[Conflict, ...]

    def day(self, sp: int) -> PlanDay:
        """The plan on day `sp`. Before today nothing has been applied (the plan starts
        today); past the last simulated day the roster stays as it ended."""
        if sp < self.today_sp:
            return PlanDay(sp, self.base)
        if sp in self.days:
            return self.days[sp]
        last = self.days[self.through_sp]
        return PlanDay(sp, last.roster, (), last.applied)


def simulate(
    actual_roster_ids: Iterable[int],
    moves: Sequence[Move],
    today_sp: int,
    through_sp: int,
) -> Simulation:
    """The plan roster for every day from `today_sp` through `through_sp` (see the module
    docstring for the rules). Moves landing after `through_sp` are not applied."""
    by_id = {move.id: move for move in moves}
    base = {pid: PlanEntry(pid, "actual") for pid in actual_roster_ids}
    roster = dict(base)
    planned = sorted((m for m in moves if m.status == PLANNED), key=lambda m: m.order_key)
    last = max(through_sp, today_sp)

    applied: list[AppliedMove] = []
    conflicts: list[Conflict] = []
    dropped_by: dict[int, int] = {}  # player -> the move that last dropped him in the plan
    days: dict[int, PlanDay] = {}
    index = 0
    for sp in range(today_sp, last + 1):
        drops: list[PlannedDrop] = []
        while index < len(planned) and landing_period(planned[index], today_sp) == sp:
            move = planned[index]
            index += 1

            dropped: int | None = None
            drop = resolve_drop(move, by_id)
            if drop.problem == "cycle":
                conflicts.append(
                    Conflict(CHAIN_CYCLE, move.id, sp, other_move_id=drop.from_move_id)
                )
            elif drop.problem is not None or drop.player_id is None:
                conflicts.append(
                    Conflict(
                        BAD_CHAIN,
                        move.id,
                        sp,
                        player_id=drop.player_id,
                        other_move_id=drop.from_move_id,
                        reason=drop.problem or "missing",
                    )
                )
            elif drop.player_id not in roster:
                conflicts.append(
                    Conflict(
                        DROP_NOT_ROSTERED,
                        move.id,
                        sp,
                        player_id=drop.player_id,
                        other_move_id=dropped_by.get(drop.player_id),
                        reason="dropped_earlier"
                        if drop.player_id in dropped_by
                        else "not_rostered",
                    )
                )
            else:
                dropped = drop.player_id
                del roster[dropped]
                dropped_by[dropped] = move.id
                drops.append(PlannedDrop(dropped, move.id, drop.alternatives))

            added: int | None = None
            pickup = effective_pickup(move)
            if pickup is not None and pickup in roster:
                holder = roster[pickup]
                if holder.status == "planned":
                    conflicts.append(
                        Conflict(
                            DOUBLE_PICKUP,
                            move.id,
                            sp,
                            player_id=pickup,
                            other_move_id=holder.move_id,
                        )
                    )
                else:
                    conflicts.append(Conflict(PICKUP_ALREADY_ROSTERED, move.id, sp, pickup))
            elif pickup is not None:
                roster[pickup] = PlanEntry(pickup, "planned", move.id, pickup_alternatives(move))
                added = pickup
            applied.append(AppliedMove(move.id, sp, added, dropped))

        days[sp] = PlanDay(sp, dict(roster), tuple(drops), tuple(applied))

    return Simulation(
        today_sp=today_sp,
        through_sp=last,
        base=base,
        days=days,
        applied=tuple(applied),
        conflicts=tuple(conflicts),
    )


def slot_substitutions(day: PlanDay, since_sp: int | None = None) -> dict[int, int]:
    """`{added_id: dropped_id}` for the moves landed on or before `day` (and after `since_sp`
    if given), so a pickup can take the slot of whoever he replaced.

    Chains compose: if move 1 swaps X for A and move 2 swaps A for C, C maps to X, because X
    is who holds the slot in the lineup the plan starts from. A move whose drop didn't happen
    has no slot to inherit, so its pickup isn't in here (he goes to the bench).
    """
    subs: dict[int, int] = {}
    for event in day.applied:
        if since_sp is not None and event.sp <= since_sp:
            continue
        if event.dropped is None:
            continue
        origin = subs.pop(event.dropped, event.dropped)
        if event.added is not None:
            subs[event.added] = origin
    return subs


# --- the Plan view's lineup -----------------------------------------------------------------

PlanLineupSource = Literal["plan_edit", "plan_carried", "from_actual"]


@dataclass(frozen=True)
class PlanLineup:
    placement: dict[int, int]
    source: PlanLineupSource
    # The plan-edited day a carried lineup comes from; None unless `plan_carried`.
    carried_from_sp: int | None = None


def plan_lineup(
    day: PlanDay,
    actual_placement: Mapping[int, int],
    plan_rows_by_day: Mapping[int, Mapping[int, int]],
    eligible: Mapping[int, Sequence[int]] | None = None,
) -> PlanLineup:
    """`{player_id: slot_id}` for the Plan view on `day`.

    The base is the stored Plan-view rows for that day, else the nearest earlier plan-edited
    day (carried forward, as `resolve_lineup` carries the Actual lineup), else the Actual
    view's lineup for that day (`actual_placement`, already resolved). Each pickup landed since
    that base takes his dropped player's slot, except into IR or a slot he isn't eligible for
    (`eligible`, when given), which send him to the bench like a pickup whose dropped player
    had no slot. Then `resolve_lineup` reconciles it with the plan roster: whoever's gone is
    removed, anyone new goes to the bench.
    """
    earlier = [d for d, rows in plan_rows_by_day.items() if d <= day.sp and rows]
    if earlier:
        base_day = max(earlier)
        base: Mapping[int, int] = plan_rows_by_day[base_day]
        since: int | None = base_day
        source: PlanLineupSource = "plan_edit" if base_day == day.sp else "plan_carried"
    else:
        base_day, base, since, source = None, actual_placement, None, "from_actual"

    substituted = dict(base)
    for added, dropped in slot_substitutions(day, since).items():
        slot = base.get(dropped)
        if slot is None:
            continue
        legal = eligible is None or slot == BENCH_SLOT or slot in (eligible.get(added) or ())
        substituted[added] = slot if legal and slot != IR_SLOT else BENCH_SLOT

    placement = resolve_lineup(
        {day.sp: substituted}, {pid: BENCH_SLOT for pid in day.roster}, day.sp
    )
    return PlanLineup(placement, source, base_day if source == "plan_carried" else None)


# --- warnings -------------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanWarning:
    code: str
    severity: Severity
    message: str
    move_id: int | None = None
    matchup_id: int | None = None
    sp: int | None = None
    detail: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanContext:
    """Everything the rules need besides the moves, loaded by the caller."""

    season: int
    today_sp: int
    names: Mapping[int, str] = field(default_factory=dict)
    # When each actual roster player joined us (RosterEntry.acquisition_date); None = unknown,
    # which `waiver_clears_at` treats as held long enough.
    acquired_at: Mapping[int, datetime | None] = field(default_factory=dict)
    # Players ESPN says are on waivers now -> when they clear (None = unknown).
    on_waivers: Mapping[int, datetime | None] = field(default_factory=dict)
    waiver_hours: int | None = None
    waiver_tz: str = "America/Los_Angeles"
    # The Pacific wall-clock time every move is assumed to happen at.
    move_time: time = time(9, 0)
    acquisition_limit: int | None = None
    # matchup id -> ESPN's adds-used count, and -> a hand-set override of it.
    espn_used: Mapping[int, int] = field(default_factory=dict)
    used_overrides: Mapping[int, int] = field(default_factory=dict)
    # (sp, player) pairs with a game. None = schedule not known, so no `no_game` infos.
    games: Set[tuple[int, int]] | None = None


def move_moment(season: int, sp: int, local_time: time) -> datetime:
    """The instant a move on day `sp` is assumed to happen: that date at `local_time` Pacific."""
    return datetime.combine(period_date(season, sp), local_time, tzinfo=PACIFIC)


def day_label(d: date) -> str:
    """`Wed 10/21`."""
    return f"{d:%a} {d.month}/{d.day}"


@dataclass(frozen=True)
class AcquisitionTally:
    matchup_id: int
    label: str
    used: int
    used_source: Literal["espn", "override"]
    planned: int
    limit: int | None

    @property
    def over(self) -> bool:
        return self.limit is not None and self.used + self.planned > self.limit


def plan_matchup_ids(moves: Iterable[Move], season: int, today_sp: int) -> list[int]:
    """The current matchup through the last one any move touches (by its landing day)."""
    current = matchup_for_period(season, today_sp)
    if current is None:
        return []
    last = current.id
    for move in moves:
        matchup = matchup_for_period(season, max(move.sp, today_sp))
        if matchup is not None:
            last = max(last, matchup.id)
    return list(range(current.id, last + 1))


def acquisition_tallies(
    moves: Iterable[Move], ctx: PlanContext, matchup_ids: Iterable[int]
) -> list[AcquisitionTally]:
    """Adds used + planned, per matchup. `used` is the override if set, else ESPN's count.
    `planned` counts `planned` moves by the day they land (an overdue one lands today, in
    today's matchup); a `done` move is already in ESPN's count."""
    planned: dict[int, int] = {}
    for move in moves:
        if move.status != PLANNED:
            continue
        matchup = matchup_for_period(ctx.season, landing_period(move, ctx.today_sp))
        if matchup is not None:
            planned[matchup.id] = planned.get(matchup.id, 0) + 1

    out = []
    for matchup_id in matchup_ids:
        matchup = matchup_by_id(ctx.season, matchup_id)
        overridden = matchup_id in ctx.used_overrides
        out.append(
            AcquisitionTally(
                matchup_id=matchup_id,
                label=matchup.label,
                used=ctx.used_overrides[matchup_id]
                if overridden
                else ctx.espn_used.get(matchup_id, 0),
                used_source="override" if overridden else "espn",
                planned=planned.get(matchup_id, 0),
                limit=ctx.acquisition_limit,
            )
        )
    return out


def warnings(moves: Sequence[Move], sim: Simulation, ctx: PlanContext) -> list[PlanWarning]:
    """Every rule, as warnings and infos. Never raises for a rule; never blocks anything."""
    by_id = {move.id: move for move in moves}

    def name(pid: int | None) -> str:
        if pid is None:
            return "nobody"
        return ctx.names.get(pid) or f"player {pid}"

    def day(sp: int) -> str:
        return day_label(period_date(ctx.season, sp))

    def matchup_id(sp: int) -> int | None:
        matchup = matchup_for_period(ctx.season, sp)
        return matchup.id if matchup else None

    out: list[PlanWarning] = []

    def add(
        code: str, severity: Severity, message: str, move: Move | None, sp: int | None, **detail
    ):
        out.append(
            PlanWarning(
                code=code,
                severity=severity,
                message=message,
                move_id=move.id if move else None,
                matchup_id=matchup_id(sp) if sp is not None else None,
                sp=sp,
                detail=detail,
            )
        )

    # 3. Roster-shape conflicts found by the simulation.
    for c in sim.conflicts:
        move = by_id[c.move_id]
        if c.code == DROP_NOT_ROSTERED:
            why = (
                f"already dropped by move {c.other_move_id}"
                if c.reason == "dropped_earlier"
                else "not on the roster"
            )
            add(
                c.code,
                "warning",
                f"Move {move.id} drops {name(c.player_id)} on {day(c.sp)}, but he's {why}; "
                "the pickup still happens",
                move,
                c.sp,
                player_id=c.player_id,
                reason=c.reason,
                other_move_id=c.other_move_id,
            )
        elif c.code == PICKUP_ALREADY_ROSTERED:
            add(
                c.code,
                "warning",
                f"Move {move.id} picks up {name(c.player_id)}, who is already on the roster "
                f"on {day(c.sp)}",
                move,
                c.sp,
                player_id=c.player_id,
            )
        elif c.code == DOUBLE_PICKUP:
            add(
                c.code,
                "warning",
                f"Moves {c.other_move_id} and {move.id} both pick up {name(c.player_id)}, "
                "with no drop of him in between",
                move,
                c.sp,
                player_id=c.player_id,
                other_move_id=c.other_move_id,
            )
        elif c.code == BAD_CHAIN:
            target = by_id.get(c.other_move_id) if c.other_move_id is not None else None
            why = {
                "missing": "which no longer exists",
                "skipped": "which is skipped",
                "later": f"which comes later ({day(target.sp)})" if target else "which comes later",
            }.get(c.reason or "missing", "which can't be resolved")
            add(
                c.code,
                "warning",
                f"Move {move.id} drops whoever move {c.other_move_id} picks up, {why}; "
                "nobody is dropped",
                move,
                c.sp,
                other_move_id=c.other_move_id,
                reason=c.reason,
            )
        elif c.code == CHAIN_CYCLE:
            add(
                c.code,
                "warning",
                f"Move {move.id}'s drop chain loops back to itself (through move "
                f"{c.other_move_id}); nobody is dropped",
                move,
                c.sp,
                other_move_id=c.other_move_id,
            )

    # 2. Waivers: ESPN's current waiver players, and players this plan drops then re-adds.
    joined: dict[int, datetime | None] = dict(ctx.acquired_at)
    # player -> (clears_at, known, dropping move). clears_at None + known = straight to FA.
    plan_drops: dict[int, tuple[datetime | None, bool, int]] = {}
    for event in sim.applied:
        move = by_id[event.move_id]
        moment = move_moment(ctx.season, event.sp, ctx.move_time)
        if event.dropped is not None:
            if ctx.waiver_hours is None:
                plan_drops[event.dropped] = (None, False, move.id)
            else:
                clears = waiver_clears_at(
                    moment, joined.get(event.dropped), ctx.waiver_hours, ctx.waiver_tz
                )
                plan_drops[event.dropped] = (clears, True, move.id)
            joined.pop(event.dropped, None)
        if event.added is None:
            continue
        pid = event.added
        joined[pid] = moment
        if pid in plan_drops:
            clears, known, dropped_in = plan_drops[pid]
            source = "plan_drop"
        elif pid in ctx.on_waivers:
            clears, known, dropped_in = ctx.on_waivers[pid], ctx.on_waivers[pid] is not None, None
            source = "espn"
        else:
            continue
        if not known:
            add(
                ON_WAIVERS,
                "warning",
                f"{name(pid)} is on waivers on {day(event.sp)} and his clear time is unknown",
                move,
                event.sp,
                player_id=pid,
                clears_at=None,
                source=source,
                dropped_in_move=dropped_in,
            )
        elif clears is not None and moment < clears:
            until = day_label(clears.astimezone(PACIFIC).date())
            add(
                ON_WAIVERS,
                "warning",
                f"{name(pid)} is on waivers until {until}, but the move is {day(event.sp)}",
                move,
                event.sp,
                player_id=pid,
                clears_at=clears.isoformat(),
                source=source,
                dropped_in_move=dropped_in,
            )

    # 1. The acquisition limit, per matchup.
    for tally in acquisition_tallies(moves, ctx, plan_matchup_ids(moves, ctx.season, ctx.today_sp)):
        if tally.over:
            out.append(
                PlanWarning(
                    code=ACQ_LIMIT,
                    severity="warning",
                    message=(
                        f"{tally.label}: {tally.used + tally.planned} of {tally.limit} "
                        f"acquisitions ({tally.used} used + {tally.planned} planned)"
                    ),
                    matchup_id=tally.matchup_id,
                    detail={
                        "used": tally.used,
                        "used_source": tally.used_source,
                        "planned": tally.planned,
                        "limit": tally.limit,
                    },
                )
            )

    # 4. A backup of one move that is also a pickup or backup of another.
    live = [m for m in sorted(moves, key=lambda m: m.order_key) if m.status == PLANNED]
    for move in live:
        for backup in pickup_alternatives(move):
            for other in live:
                if other.id == move.id:
                    continue
                if backup == effective_pickup(other):
                    role = "the pickup"
                elif backup in pickup_alternatives(other):
                    role = "a backup"
                else:
                    continue
                add(
                    BACKUP_OVERLAP,
                    "warning",
                    f"{name(backup)} is a backup in move {move.id} and {role} in move {other.id}",
                    move,
                    landing_period(move, ctx.today_sp),
                    player_id=backup,
                    other_move_id=other.id,
                )

    # 5. Infos: no game on the move day, overdue moves, and an ESPN move that dropped someone
    # other than planned.
    for move in live:
        sp = landing_period(move, ctx.today_sp)
        pickup = effective_pickup(move)
        if ctx.games is not None and pickup is not None and (sp, pickup) not in ctx.games:
            add(
                NO_GAME,
                "info",
                f"{name(pickup)} has no game on {day(sp)} (move {move.id})",
                move,
                sp,
                player_id=pickup,
            )
        if move.sp < ctx.today_sp:
            add(
                OVERDUE,
                "info",
                f"Move {move.id} was planned for {day(move.sp)} and hasn't been made yet",
                move,
                sp,
                planned_sp=move.sp,
            )
    for move in moves:
        if move.status == DONE and move.drop_mismatch:
            planned_drop = resolve_drop(move, by_id).player_id
            dropped = move.detected_drop_player_id
            what = f"you dropped {name(dropped)}" if dropped is not None else "nobody was dropped"
            add(
                DIFFERENT_DROP,
                "info",
                f"Move {move.id} was made on ESPN, but {what}, not {name(planned_drop)}",
                move,
                move.sp,
                planned_drop=planned_drop,
                dropped=dropped,
            )
    return out
