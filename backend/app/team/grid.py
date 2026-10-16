"""The roster grid, lineup-edit rules and the waiver list's day filter. Pure: no database.

Slot ids are ESPN's (espn_api `POSITION_MAP`): PG 0, SG 1, SF 2, PF 3, C 4, G 5, F 6, ...,
UT 11, BE 12, IR 13. `LeagueSettings.roster_slots` stores counts by NAME, so everything here
takes the name-keyed counts and converts once.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from espn_api.basketball.constant import POSITION_MAP

from app.team.lineup import BENCH_SLOT, IR_SLOT

# Healthy, as ESPN spells it on `player.injuryStatus` (None when it says nothing).
HEALTHY_INJURY_STATUSES = frozenset({None, "ACTIVE", "NORMAL"})

DayFilterMode = Literal["none", "any", "all"]


def slot_name(slot_id: int) -> str:
    return POSITION_MAP.get(slot_id) or f"SLOT_{slot_id}"


def slot_counts_by_id(roster_slots: Mapping[str, int] | None) -> dict[int, int]:
    """`{'PG': 1, 'UT': 2, ...}` -> `{0: 1, 11: 2, ...}`, zero counts and unknown names dropped."""
    out: dict[int, int] = {}
    for name, count in (roster_slots or {}).items():
        slot_id = POSITION_MAP.get(name)
        if isinstance(slot_id, int) and int(count) > 0:
            out[slot_id] = int(count)
    return out


@dataclass(frozen=True)
class GridRow:
    slot_id: int
    slot: str
    player_id: int | None


def build_slot_grid(
    slot_counts: Mapping[int, int],
    placement: Mapping[int, int],
    order: Sequence[int] | None = None,
) -> tuple[list[GridRow], list[str]]:
    """ESPN's roster grid: one row per slot, slot-id order (PG, SG, SF, PF, C, UT, BE, IR).

    `placement` is `{player_id: slot_id}`; `order` is the order players fill a multi-row slot
    in (default: `placement`'s). Empty slots are rows with no player, as ESPN shows them. The
    bench grows past its count to fit everyone, so nobody is ever hidden. A slot holding more
    players than it has room for (or one the league doesn't have) shouldn't happen, but if it
    does the extras go to the end of the bench, with a warning.
    """
    ordered = [pid for pid in (order or placement) if pid in placement]
    by_slot: dict[int, list[int]] = {}
    for pid in ordered:
        by_slot.setdefault(placement[pid], []).append(pid)

    warnings: list[str] = []
    overflow: list[int] = []
    for slot_id, pids in sorted(by_slot.items()):
        if slot_id == BENCH_SLOT:
            continue
        room = slot_counts.get(slot_id, 0)
        if len(pids) > room:
            extras = pids[room:]
            overflow.extend(extras)
            by_slot[slot_id] = pids[:room]
            warnings.append(
                f"{len(pids)} players in {slot_name(slot_id)}, which has room for {room}; "
                f"{len(extras)} shown on the bench"
            )

    bench = by_slot.get(BENCH_SLOT, []) + overflow
    rows: list[GridRow] = []
    for slot_id in sorted(set(slot_counts) | {BENCH_SLOT}):
        pids = bench if slot_id == BENCH_SLOT else by_slot.get(slot_id, [])
        size = max(slot_counts.get(slot_id, 0), len(pids))
        name = slot_name(slot_id)
        rows.extend(
            GridRow(slot_id, name, pids[index] if index < len(pids) else None)
            for index in range(size)
        )
    return rows, warnings


@dataclass(frozen=True)
class LineupCandidate:
    """What the edit rules need to know about one rostered player."""

    player_id: int
    name: str
    eligible_slots: Sequence[int] | None
    injury_status: str | None


def validate_lineup(
    assignments: Sequence[tuple[int, int]],
    roster: Mapping[int, LineupCandidate],
    slot_counts: Mapping[int, int],
) -> tuple[list[str], list[str]]:
    """`(errors, warnings)` for a proposed `[(player_id, slot_id)]` lineup. Errors block it.

    Every rostered player exactly once and nobody else; each slot within its count (the bench
    takes anyone up to roster size); each slot one the player is eligible for (the bench is
    always allowed). IR needs IR in his eligible slots, which ESPN gives everyone, so a healthy
    player on IR is only a warning.
    """
    errors: list[str] = []
    warnings: list[str] = []

    def label(pid: int) -> str:
        candidate = roster.get(pid)
        return f"{candidate.name} ({pid})" if candidate else f"player {pid}"

    seen: dict[int, int] = {}
    for pid, _ in assignments:
        seen[pid] = seen.get(pid, 0) + 1
    for pid, times in seen.items():
        if pid not in roster:
            errors.append(f"{label(pid)} is not on the roster")
        elif times > 1:
            errors.append(f"{label(pid)} is assigned {times} times")
    errors.extend(f"{label(pid)} is missing from the lineup" for pid in roster if pid not in seen)

    used: dict[int, int] = {}
    for pid, slot_id in dict(assignments).items():
        used[slot_id] = used.get(slot_id, 0) + 1
        candidate = roster.get(pid)
        if candidate is None or slot_id == BENCH_SLOT:
            continue
        if slot_id not in (candidate.eligible_slots or []):
            errors.append(f"{label(pid)} can't play {slot_name(slot_id)}")
        elif slot_id == IR_SLOT and candidate.injury_status in HEALTHY_INJURY_STATUSES:
            warnings.append(f"{label(pid)} is on IR but not listed as injured")

    for slot_id, count in sorted(used.items()):
        room = len(roster) if slot_id == BENCH_SLOT else slot_counts.get(slot_id, 0)
        if count > room:
            errors.append(f"{count} players in {slot_name(slot_id)}, which has room for {room}")
    return errors, warnings


def passes_day_filter(plays_on: Iterable[bool], mode: DayFilterMode) -> bool:
    """`none` keeps everyone; `any` needs a game on one of the days; `all` on every one."""
    flags = list(plays_on)
    if mode == "any":
        return any(flags)
    if mode == "all":
        return bool(flags) and all(flags)
    return True
