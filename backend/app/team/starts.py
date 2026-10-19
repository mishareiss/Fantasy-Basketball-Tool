"""The starts tracker: starts filled against starts possible, per day and per matchup. Pure:
no database, no ESPN.

H2H points is won on STARTS: every starting slot (PG, SG, SF, PF, C, UT, UT) on every day of
the matchup. A day's `my_games` count says how many of mine have a game, which misleads both
ways: 14 with games still fill only 7 slots, and 4 with games leave 3 empty. So, per day:

* `filled`: starting slots held, in the lineup AS SET, by a player who plays.
* `possible`: how many the optimizer (`app.team.autofill.best_lineup`) fills with players who
  play from that day's roster. Positions matter: eight guards fill five slots, not seven.
* `empty` = slots - possible: no roster player can fill them. Streaming opportunities.
* `left_on_bench` = possible - filled: starts lost to the lineup as set. Auto-fill fixes them.

"Plays" is auto-fill's rule (a game, and not OUT or SUSPENSION), and IR never counts.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.team.autofill import AutoFillPlayer, best_lineup, starting_slots
from app.team.lineup import BENCH_SLOT, IR_SLOT


@dataclass(frozen=True)
class DayStarts:
    sp: int
    slots: int
    filled: int
    possible: int
    # Players who play but sit even in the optimizer's lineup: no slot left that fits them.
    benched_with_games: tuple[int, ...]
    projected_points: float
    projected_points_as_set: float
    past: bool = False

    @property
    def empty(self) -> int:
        return self.slots - self.possible

    @property
    def left_on_bench(self) -> int:
        return max(0, self.possible - self.filled)


@dataclass(frozen=True)
class MatchupStarts:
    days: int
    max: int
    filled: int
    possible: int
    empty: int
    left_on_bench: int
    projected_points: float
    projected_points_as_set: float


def _starting(slot_counts: Mapping[int, int]) -> set[int]:
    return {slot for slot in slot_counts if slot not in (BENCH_SLOT, IR_SLOT)}


def filled_starts(
    players: Iterable[AutoFillPlayer], slot_counts: Mapping[int, int]
) -> list[AutoFillPlayer]:
    """The players who play in a starting slot of the lineup as set (`slot_id`), never more
    per slot than the slot has rows (best value first, should a stored lineup overfill one)."""
    by_slot: dict[int, list[AutoFillPlayer]] = {}
    starting = _starting(slot_counts)
    for player in players:
        if player.plays and player.slot_id in starting:
            by_slot.setdefault(player.slot_id, []).append(player)
    out: list[AutoFillPlayer] = []
    for slot_id, group in by_slot.items():
        group.sort(key=lambda p: (-p.value, p.id))
        out.extend(group[: slot_counts[slot_id]])
    return out


def day_starts(
    sp: int,
    players: Sequence[AutoFillPlayer],
    slot_counts: Mapping[int, int],
    past: bool = False,
) -> DayStarts:
    """One day's numbers. Each player's `slot_id` is where the lineup as set has him."""
    starting = _starting(slot_counts)
    best = best_lineup(players, slot_counts, leave_empty=True)
    playing = [p for p in players if p.plays and p.slot_id != IR_SLOT]
    starters = [p for p in playing if best[p.id] in starting]
    benched = sorted(
        (p for p in playing if best[p.id] not in starting), key=lambda p: (-p.value, p.id)
    )
    as_set = filled_starts(players, slot_counts)
    return DayStarts(
        sp=sp,
        slots=len(starting_slots(slot_counts)),
        filled=len(as_set),
        possible=len(starters),
        benched_with_games=tuple(p.id for p in benched),
        projected_points=round(sum(p.value for p in starters), 2),
        projected_points_as_set=round(sum(p.value for p in as_set), 2),
        past=past,
    )


def matchup_starts(days: Sequence[DayStarts]) -> MatchupStarts:
    """The matchup's totals: every day's numbers summed, and `max` = slots x days."""
    return MatchupStarts(
        days=len(days),
        max=sum(d.slots for d in days),
        filled=sum(d.filled for d in days),
        possible=sum(d.possible for d in days),
        empty=sum(d.empty for d in days),
        left_on_bench=sum(d.left_on_bench for d in days),
        projected_points=round(sum(d.projected_points for d in days), 2),
        projected_points_as_set=round(sum(d.projected_points_as_set for d in days), 2),
    )
