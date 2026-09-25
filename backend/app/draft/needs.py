"""What a team still NEEDS, under the only definition that survives a 20-round startup.

In a league with 13 bench slots, "need" in the usual sense doesn't exist. Every team fills a
legal 20 no matter what it drafts, so nothing here is a constraint — there is no pick this
module can forbid, and it never tries to. What it answers is narrower and softer: *does this
player fill a hole in that team's starting five right now*, which is the one thing that
plausibly tilts a real drafter's hand away from best-available.

THE RULE, in one line: a team's need is its set of open DEDICATED starter slots — the
PG/SG/SF/PF/C counts from `lineupSlotCounts`, minus the ones already filled by an eligible
player it drafted.

UT IS POSITIONLESS, and that is the whole reason this stays small. Two utility slots and
thirteen bench slots take anybody, so they cannot make one player more wanted than another.
The consequence is worth stating plainly because it shapes every simulated draft: once a team
has covered all five dedicated positions — usually somewhere in the middle rounds — NOTHING
fills a need for it any more, `fills_need` is False for every player alive, and its picks
become pure best-available for the rest of the draft. That is intended. A team with a starting
five already set has no positional reason to prefer anyone.

SLOT ASSIGNMENT is greedy in draft order: each drafted player takes an open dedicated slot he
is eligible for if there is one, else a utility slot, else the bench. Greedy, so it is not
optimal — a PG/SG drafted while both PG and SG are open takes PG (the `DEDICATED_POSITIONS`
order), and a later PG-only arrival then reads as filling a need when a smarter assignment
would have freed SG for him instead. That is acceptable here and would not be in a lineup
optimizer: the output feeds a 1.5x nudge on a weighted random draw, not a legality check.

Pure — dicts and strings in, dicts and strings out.
"""

from collections.abc import Container, Iterable, Mapping

from app.draft.config import BENCH_SLOT, DEDICATED_POSITIONS, UTILITY_SLOT


def normalize_positions(positions: Iterable[str] | None) -> tuple[str, ...]:
    """A player's eligibility, uppercased and de-blanked, duplicates dropped, order kept.

    `Player.positions` is a JSON column written by the ESPN sync, so it is trusted about as
    far as any JSON column: a stray lowercase 'pg' would otherwise silently match nothing and
    the player would read as filling no need at any position, forever.
    """
    seen: list[str] = []
    for position in positions or ():
        cleaned = str(position).strip().upper()
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def fills_need(player_positions: Iterable[str], open_dedicated: Container[str]) -> bool:
    """Does this player cover one of the dedicated starter slots this team still has open?

    ANY of his positions is enough: a PG/SG fills a hole at either, which is exactly the
    flexibility a multi-position player is worth in a startup. Returns False for everyone once
    `open_dedicated` is empty, which is the UT/bench rule above doing its job.
    """
    return any(position in open_dedicated for position in normalize_positions(player_positions))


class RosterFill:
    """One team's slot ledger: what is still open, and what each pick took.

    Mutable and incremental on purpose. The Monte Carlo runs tens of thousands of simulated
    picks, and recomputing a team's starting five from its roster at each one would be the
    hot loop; instead a pick decrements one counter here and the answer is already current.
    """

    __slots__ = ("_dedicated", "_utility", "_bench", "_assignments")

    def __init__(self, roster_slots: Mapping[str, int]) -> None:
        self._dedicated: dict[str, int] = {
            position: roster_slots[position]
            for position in DEDICATED_POSITIONS
            if roster_slots.get(position, 0) > 0
        }
        self._utility: int = roster_slots.get(UTILITY_SLOT, 0)
        self._bench: int = roster_slots.get(BENCH_SLOT, 0)
        # The slot each drafted player took, in draft order. Not used by the engine — it is
        # what makes a greedy assignment inspectable when a need reads wrong.
        self._assignments: list[str] = []

    @property
    def open_dedicated(self) -> frozenset[str]:
        """The dedicated positions still unfilled. Empty once the starting five is set."""
        return frozenset(
            position for position, remaining in self._dedicated.items() if remaining > 0
        )

    @property
    def assignments(self) -> tuple[str, ...]:
        """Which slot each drafted player took, in draft order."""
        return tuple(self._assignments)

    def fills_need(self, player_positions: Iterable[str]) -> bool:
        return fills_need(player_positions, self.open_dedicated)

    def add(self, player_positions: Iterable[str]) -> str:
        """Place a drafted player and return the slot name he took.

        Greedy, in `DEDICATED_POSITIONS` order: an open dedicated slot he is eligible for,
        else utility, else bench. Past 20 picks there is nothing left to take and the slot
        reads `BENCH_SLOT` anyway — the engine bounds a roster by `rounds`, not by this.
        """
        for position in normalize_positions(player_positions):
            if self._dedicated.get(position, 0) > 0:
                self._dedicated[position] -= 1
                self._assignments.append(position)
                return position
        if self._utility > 0:
            self._utility -= 1
            self._assignments.append(UTILITY_SLOT)
            return UTILITY_SLOT
        self._bench -= 1
        self._assignments.append(BENCH_SLOT)
        return BENCH_SLOT

    def copy(self) -> "RosterFill":
        """An independent ledger — what a forked simulation drafts can't reach the original."""
        clone = RosterFill.__new__(RosterFill)
        clone._dedicated = dict(self._dedicated)
        clone._utility = self._utility
        clone._bench = self._bench
        clone._assignments = list(self._assignments)
        return clone

    def __repr__(self) -> str:
        return (
            f"RosterFill(open_dedicated={sorted(self.open_dedicated)!r}, "
            f"utility={self._utility}, bench={self._bench})"
        )


def fill_roster(
    roster_slots: Mapping[str, int], rostered_positions: Iterable[Iterable[str]]
) -> RosterFill:
    """The ledger a team arrives at after drafting these players, in this order.

    The from-scratch statement of the same rule `DraftState` maintains incrementally — handy
    to assert the rule against, and the thing to reach for when a roster comes from somewhere
    other than a pick-by-pick draft.
    """
    ledger = RosterFill(roster_slots)
    for positions in rostered_positions:
        ledger.add(positions)
    return ledger
