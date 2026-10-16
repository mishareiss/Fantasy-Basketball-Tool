"""Auto-fill: the best lineup for one day. Pure: no database, no ESPN.

Every starting slot (anything but the bench and IR) goes to a player who plays that day if one
can legally fill it, the best of them by value; the rest sit. Solved exactly with a DP over
which starting slots are filled, so a position puzzle a greedy PG-first fill would get wrong
(a C-only player stranded behind a PF/C in the C slot) comes out right.
"""

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from app.team.lineup import BENCH_SLOT, IR_SLOT

# Injury statuses that keep a player with a game off the floor. DAY_TO_DAY still plays.
NOT_PLAYING_STATUSES = frozenset({"OUT", "SUSPENSION"})


@dataclass(frozen=True)
class AutoFillPlayer:
    id: int
    eligible_slots: Sequence[int]
    slot_id: int
    # A game on the day. Whether he *plays* also depends on his injury status (`plays`).
    has_game: bool
    value: float
    injury_status: str | None = None

    @property
    def plays(self) -> bool:
        return self.has_game and self.injury_status not in NOT_PLAYING_STATUSES


def starting_slots(slot_counts: Mapping[int, int]) -> list[int]:
    """One entry per starting position, slot-id order: `[0, 1, 2, 3, 4, 11, 11]`."""
    return [
        slot_id
        for slot_id in sorted(slot_counts)
        if slot_id not in (BENCH_SLOT, IR_SLOT)
        for _ in range(slot_counts[slot_id])
    ]


def _points(value: float) -> int:
    """Values compared as integers, so a sum never depends on the order it was added in."""
    return round(value * 1000)


def best_lineup(
    players: Sequence[AutoFillPlayer],
    slot_counts: Mapping[int, int],
    locked: Collection[int] = (),
    leave_empty: bool = False,
) -> dict[int, int]:
    """`{player_id: slot_id}` for every player.

    IR stays IR and a `locked` player keeps his slot; everyone else is free. The free players
    are placed to maximize, in order: (1) starting slots held by players who play, (2) the sum
    of those starters' values, (3) starting slots held by players who don't play (none if
    `leave_empty`), (4) those players' values. Ties go to the higher-value player, then the
    lower id. Anyone not starting goes to the bench.
    """
    out: dict[int, int] = {}
    open_slots = starting_slots(slot_counts)
    free: list[AutoFillPlayer] = []
    for player in players:
        if player.slot_id == IR_SLOT or player.id in locked:
            out[player.id] = player.slot_id
            if player.slot_id in open_slots:
                open_slots.remove(player.slot_id)
        else:
            free.append(player)

    # Best first: the tiebreak below prefers lineups that include earlier players.
    free.sort(key=lambda p: (-p.value, p.id))
    n = len(free)
    # dp[mask] = (score, placements): mask = which of `open_slots` are filled.
    dp: dict[int, tuple[tuple[int, ...], tuple[tuple[int, int], ...]]] = {0: ((0,) * 5, ())}
    for rank, player in enumerate(free):
        if not player.plays and leave_empty:
            continue
        weight = 1 << (n - 1 - rank)
        gain = (
            (1, _points(player.value), 0, 0, weight)
            if player.plays
            else (0, 0, 1, _points(player.value), weight)
        )
        eligible = set(player.eligible_slots)
        nxt = dict(dp)
        for mask, (score, placed) in dp.items():
            seen: set[int] = set()
            for index, slot_id in enumerate(open_slots):
                # Two free rows of the same slot (UT, UT) are the same choice.
                if mask & (1 << index) or slot_id not in eligible or slot_id in seen:
                    continue
                seen.add(slot_id)
                new_mask = mask | (1 << index)
                new_score = tuple(a + b for a, b in zip(score, gain, strict=True))
                if new_mask not in nxt or new_score > nxt[new_mask][0]:
                    nxt[new_mask] = (new_score, (*placed, (player.id, slot_id)))
        dp = nxt

    _, placed = max(dp.values(), key=lambda entry: entry[0])
    starters = dict(placed)
    for player in free:
        out[player.id] = starters.get(player.id, BENCH_SLOT)
    return out
