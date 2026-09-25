"""The shape of the draft: how many teams, how many rounds, where I sit, and what a roster is.

Pure, and deliberately the only module in this package that knows a `Settings` object can
exist — and even that only as a type, under `TYPE_CHECKING`, so importing the engine never
drags the environment (or a database) in behind it. Everything below is arithmetic on four
numbers, which is what lets the snake be asserted on a 4-team toy instead of on our league.

THE SNAKE is the whole reason this is a module and not a constant. Round 1 goes 1..N, round 2
goes N..1, and so on, which means my pick numbers are not an arithmetic sequence: at slot 2 of
10 they run 2, 19, 22, 39, 42, ... — a 17-pick wait, then a 3-pick wait, forever alternating.
That alternation IS the draft plan problem (who survives seventeen picks?), so the order is
derived once here and every other module reads it rather than re-deriving a half-version of it.

ROSTER SLOTS arrive as ESPN's `lineupSlotCounts` shape — `{'PG': 1, ..., 'UT': 2, 'BE': 13}`
(`app.db.models.league_settings.LeagueSettings.roster_slots`) — but as a PLAIN DICT, passed in.
The engine never queries the league; a hypothetical roster is as valid an input as the real one.

What the counts are used for is narrower than it looks: only the five DEDICATED starter slots
(PG/SG/SF/PF/C) create positional need, because UT and the bench are positionless and anyone
fills them (see `app.draft.needs`). `rounds`, not `roster_size`, is what bounds a roster here —
a 20-round draft fills 20 slots whatever the settings row happens to add up to.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - a type-only import, so the engine stays stdlib-pure
    from app.config import Settings

# The five atomic positions ESPN lists a player at, in the order a lineup card prints them.
# A dedicated starter slot named one of these is the only kind of slot that creates need.
DEDICATED_POSITIONS: tuple[str, ...] = ("PG", "SG", "SF", "PF", "C")

# The two positionless slot names in `lineupSlotCounts`. Anyone fills either.
UTILITY_SLOT = "UT"
BENCH_SLOT = "BE"

# Our league, as agreed: 10 teams, 20 rounds, Misha at slot 2. These are ALSO the `DRAFT_*`
# defaults in `app.config.Settings` — kept in step by `test_draft_engine`, which asserts
# `DraftConfig.from_settings(get_settings()) == DraftConfig()` rather than trusting a comment.
# `Settings` can't import them (it would close a cycle through `app.draft.field`'s database
# imports), so the duplication is guarded by a test instead of by an import.
DEFAULT_TEAM_COUNT = 10
DEFAULT_ROUNDS = 20
DEFAULT_MY_SLOT = 2

# The startup roster: five dedicated starters, two utility, thirteen bench. Twenty slots for
# twenty rounds, which is why no team ever has to make a legal-lineup decision mid-draft.
DEFAULT_ROSTER_SLOTS: Mapping[str, int] = MappingProxyType(
    {"PG": 1, "SG": 1, "SF": 1, "PF": 1, "C": 1, UTILITY_SLOT: 2, BENCH_SLOT: 13}
)

# --- opponent auto-pick ------------------------------------------------------------------------
# See `app.draft.autopick` for what each one does to the sampled pick. Same story as above:
# these are the `DRAFT_AUTOPICK_*` / `DRAFT_SIM_*` defaults in `Settings`, guarded by a test.

# How many still-available players the field will even consider. Twelve is about a round of
# names: wide enough that the room doesn't draft one deterministic order every iteration,
# narrow enough that nobody reaches forty picks early.
DEFAULT_AUTOPICK_TOP_K = 12
# The softmax temperature, in PLACES. At 8.0 a player ranked eight spots better is e (~2.7)
# times likelier to go. Raise it to make the field draftier and more random; lower it towards
# 0 to make the field draft the consensus board straight down.
DEFAULT_AUTOPICK_TEMPERATURE = 8.0
# The tilt applied to a candidate who fills one of that team's OPEN dedicated starter slots.
# 1.5 is a soft nudge on purpose: 1.0 would be pure best-available, and a hard constraint
# would have every team reaching for a centre in round 3 whether or not one was worth it.
DEFAULT_AUTOPICK_NEED_MULT = 1.5

# Monte-Carlo iterations behind an availability number. A thousand puts the standard error on
# a 50% answer at ~1.6 points, which is finer than the question deserves.
DEFAULT_SIM_ITERATIONS = 1000
# The default RNG seed. Availability is a number people compare between two refreshes, so the
# same committed state has to give the same answer twice; a seed is how.
DEFAULT_SIM_SEED = 0


@dataclass(frozen=True)
class DraftConfig:
    """Team count, rounds, roster shape and my seat — with the snake derived once.

    Frozen for the same reason `TierParams` is: one draft is one shape, and a `my_slot` that
    could be reassigned halfway through a simulation would silently re-label picks already made.
    """

    team_count: int = DEFAULT_TEAM_COUNT
    rounds: int = DEFAULT_ROUNDS
    # ESPN's `lineupSlotCounts` shape. Copied into a read-only view at construction, so a
    # caller's dict can't be mutated out from under a running draft.
    roster_slots: Mapping[str, int] = DEFAULT_ROSTER_SLOTS
    # 1-based seat, counted the way round 1 runs: slot 2 picks second.
    my_slot: int = DEFAULT_MY_SLOT

    # Derived in `__post_init__`, never passed. `pick_order[i]` is the team slot that owns
    # pick number i + 1 — the snake, flattened, so `pick_slot` is an index and not a modulo
    # puzzle re-solved at every call site.
    pick_order: tuple[int, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the shape and flatten the snake, naming the setting that's wrong.

        These numbers reach us from the environment (`DRAFT_*`) or from a league settings row,
        so the failure mode without this is a draft that runs happily against nonsense — a
        `my_slot` of 12 in a 10-team league would simply never come up, and the plan would be
        silently computed for a seat that doesn't exist.
        """
        if not isinstance(self.team_count, int) or self.team_count < 2:
            raise ValueError(f"DRAFT_TEAM_COUNT ({self.team_count!r}) must be an int >= 2")
        if not isinstance(self.rounds, int) or self.rounds < 1:
            raise ValueError(f"DRAFT_ROUNDS ({self.rounds!r}) must be an int >= 1")
        if not isinstance(self.my_slot, int) or not 1 <= self.my_slot <= self.team_count:
            raise ValueError(
                f"DRAFT_MY_SLOT ({self.my_slot!r}) must be an int in 1..{self.team_count}"
            )

        slots = dict(self.roster_slots)
        for name, count in slots.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"roster_slots key {name!r} must be a non-empty slot name")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise ValueError(f"roster_slots[{name!r}] ({count!r}) must be an int >= 0")
        if sum(slots.values()) < 1:
            raise ValueError("roster_slots must open at least one slot")

        object.__setattr__(self, "roster_slots", MappingProxyType(slots))
        object.__setattr__(self, "pick_order", _snake(self.team_count, self.rounds))

    # --- the roster shape ----------------------------------------------------------------

    @property
    def dedicated_counts(self) -> dict[str, int]:
        """Position -> how many DEDICATED starter slots it opens. The only source of need.

        Positions with no slot are absent rather than zero, so `in` reads as "this league
        starts one of these". `UT` and `BE` are not here by construction: they take anyone,
        so they cannot make one player more wanted than another.
        """
        return {
            position: self.roster_slots[position]
            for position in DEDICATED_POSITIONS
            if self.roster_slots.get(position, 0) > 0
        }

    @property
    def utility_slots(self) -> int:
        return self.roster_slots.get(UTILITY_SLOT, 0)

    @property
    def bench_slots(self) -> int:
        return self.roster_slots.get(BENCH_SLOT, 0)

    @property
    def roster_size(self) -> int:
        """Every slot the settings row opens. Informational: `rounds` is what actually binds.

        A 20-round draft hands each team 20 players whatever this adds up to — an ESPN row
        carrying an IR slot would count it here and no draft pick would ever fill it.
        """
        return sum(self.roster_slots.values())

    # --- the snake -----------------------------------------------------------------------

    @property
    def total_picks(self) -> int:
        return self.team_count * self.rounds

    def pick_slot(self, pick_number: int) -> int:
        """Which team owns this 1-based pick number.

        Raises rather than wrapping: an out-of-range pick number is a bug in the caller's
        clock arithmetic, and silently returning slot 1 for pick 201 would hide it.
        """
        if not 1 <= pick_number <= self.total_picks:
            raise ValueError(
                f"pick_number {pick_number} is outside 1..{self.total_picks} "
                f"({self.team_count} teams x {self.rounds} rounds)"
            )
        return self.pick_order[pick_number - 1]

    def round_of(self, pick_number: int) -> int:
        """The 1-based round a pick number falls in."""
        if not 1 <= pick_number <= self.total_picks:
            raise ValueError(f"pick_number {pick_number} is outside 1..{self.total_picks}")
        return (pick_number - 1) // self.team_count + 1

    def pick_numbers_for(self, team_slot: int) -> list[int]:
        """Every pick number this seat owns, in order — `rounds` of them, always."""
        if not 1 <= team_slot <= self.team_count:
            raise ValueError(f"team_slot {team_slot} is outside 1..{self.team_count}")
        return [number for number, slot in enumerate(self.pick_order, start=1) if slot == team_slot]

    @property
    def my_pick_numbers(self) -> list[int]:
        """My seat's pick numbers: [2, 19, 22, 39, 42, ...] at slot 2 of 10.

        The alternating 17-then-3 wait is the reason availability is a Monte Carlo and not a
        lookup: the answer at pick 19 is a different question from the answer at pick 22.
        """
        return self.pick_numbers_for(self.my_slot)

    def is_my_pick(self, pick_number: int) -> bool:
        return self.pick_slot(pick_number) == self.my_slot

    # --- construction --------------------------------------------------------------------

    @classmethod
    def from_settings(
        cls, settings: "Settings", *, roster_slots: Mapping[str, int] | None = None
    ) -> "DraftConfig":
        """Build the configured draft — `DRAFT_*` for the shape, our startup roster for slots.

        `roster_slots` is the one thing that does NOT come from the environment, because it
        isn't a tuning knob: it is a fact about the league, and the place it lives is
        `LeagueSettings.roster_slots`. A caller that has that row passes it here; a caller
        that doesn't gets the agreed 5/2/13 startup roster.
        """
        return cls(
            team_count=settings.draft_team_count,
            rounds=settings.draft_rounds,
            roster_slots=DEFAULT_ROSTER_SLOTS if roster_slots is None else roster_slots,
            my_slot=settings.draft_my_slot,
        )


def _snake(team_count: int, rounds: int) -> tuple[int, ...]:
    """The flattened snake: 1..N, then N..1, then 1..N, ... for `rounds` rounds."""
    forward = tuple(range(1, team_count + 1))
    backward = forward[::-1]
    order: list[int] = []
    for round_number in range(1, rounds + 1):
        order.extend(forward if round_number % 2 else backward)
    return tuple(order)
