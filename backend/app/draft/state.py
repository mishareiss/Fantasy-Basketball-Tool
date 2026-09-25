"""Who has been taken, by whom, and who is on the clock. The one mutable thing in the engine.

A draft is a list of picks and nothing else — everything below is a view over that list, kept
current incrementally rather than recomputed, because the Monte Carlo applies tens of
thousands of picks and a state that rebuilt its rosters per pick would be the whole cost.

TWO WAYS TO ADVANCE, which is the shape the draft room needs:

* `apply_pick(player_id)` — commit a real selection for the team on the clock. This is manual
  mode entering every team's pick by hand, and it is also how a completed selection of mine is
  recorded. Strictly in pick order: the draft has one clock, and a state that let pick 22 be
  entered before pick 19 could not say who was available when.
* `pass_pick()` — consume the pick number on the clock WITHOUT selecting anyone.

`pass_pick` exists for exactly one reason, and it is the central modelling choice of this
engine: **the simulation never picks for me.** Projecting forward past my own pick to ask what
survives until the next one, the honest thing to assume about the player I am about to take is
nothing at all — I have not decided yet, and a simulated me who grabbed the consensus best
available would remove him from my own availability numbers, which is precisely backwards.
So the clock advances over my seat and the board is untouched. A passed pick is a
`Pick` with `player_id=None`; it is simulation-only, and `selections` is the view that leaves
them out (that is the one Task 20 persists).

Pure: stdlib only. The draftable universe and the position map arrive as plain collections, so
a whole draft can be exercised on a dozen hand-written players.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.draft.config import DraftConfig
from app.draft.needs import RosterFill, normalize_positions


@dataclass(frozen=True)
class Pick:
    """One consumed pick number: whose it was, and who (if anyone) it took."""

    # 1-based, in the flattened snake order of `DraftConfig.pick_order`.
    pick_number: int
    team_slot: int
    # None ONLY for a passed pick — the simulation stepping over my seat (see module docstring).
    player_id: int | None


class DraftState:
    """The picks made so far, plus the views every other module reads off them."""

    __slots__ = ("_config", "_universe", "_positions", "_picks", "_available", "_rosters", "_fills")

    def __init__(
        self,
        config: DraftConfig,
        universe: Iterable[int],
        positions: Mapping[int, Sequence[str]] | None = None,
    ) -> None:
        """A draft not yet started, over this universe of draftable players.

        `universe` is who can be drafted at all — in practice the players the field has a rank
        for, since a player nobody ranks is not someone the field will take. A player outside
        it is refused by `apply_pick` rather than quietly admitted, because the alternative is
        an availability number computed over a board the simulation never actually draws from.

        `positions` maps player id -> ESPN's eligibility list. A player missing from it simply
        has no positions, fills no dedicated slot, and lands in utility or on the bench —
        which is the right behaviour for a player we hold no position data for.
        """
        self._config = config
        self._universe: frozenset[int] = frozenset(universe)
        self._positions: dict[int, tuple[str, ...]] = {
            player_id: normalize_positions(value) for player_id, value in (positions or {}).items()
        }
        self._picks: list[Pick] = []
        self._available: set[int] = set(self._universe)
        self._rosters: dict[int, list[int]] = {slot: [] for slot in range(1, config.team_count + 1)}
        self._fills: dict[int, RosterFill] = {
            slot: RosterFill(config.roster_slots) for slot in range(1, config.team_count + 1)
        }

    # --- what it is ------------------------------------------------------------------------

    @property
    def config(self) -> DraftConfig:
        return self._config

    @property
    def universe(self) -> frozenset[int]:
        """Everyone who could ever be drafted here."""
        return self._universe

    @property
    def picks(self) -> tuple[Pick, ...]:
        """Every consumed pick number in order, passed ones included."""
        return tuple(self._picks)

    @property
    def selections(self) -> tuple[Pick, ...]:
        """Only the picks that actually took a player — the real draft log."""
        return tuple(pick for pick in self._picks if pick.player_id is not None)

    def positions(self, player_id: int) -> tuple[str, ...]:
        """This player's eligibility; empty for a player we have no position data for."""
        return self._positions.get(player_id, ())

    # --- the clock -------------------------------------------------------------------------

    @property
    def next_pick_number(self) -> int | None:
        """The 1-based pick number about to be made, or None once the draft is complete."""
        number = len(self._picks) + 1
        return number if number <= self._config.total_picks else None

    @property
    def on_the_clock(self) -> int | None:
        """The team slot that owns the next pick, or None once the draft is complete."""
        number = self.next_pick_number
        return None if number is None else self._config.pick_slot(number)

    @property
    def is_complete(self) -> bool:
        return len(self._picks) >= self._config.total_picks

    @property
    def is_my_pick(self) -> bool:
        """Is my seat on the clock right now? False once the draft is over."""
        return self.on_the_clock == self._config.my_slot

    # --- the board -------------------------------------------------------------------------

    @property
    def available(self) -> frozenset[int]:
        """Everyone still on the board. A snapshot copy — mutating it does nothing here."""
        return frozenset(self._available)

    @property
    def drafted(self) -> frozenset[int]:
        """Everyone taken so far, across all teams."""
        return self._universe - self._available

    def is_available(self, player_id: int) -> bool:
        """O(1), and the one every hot loop uses — `available` copies a thousand-element set."""
        return player_id in self._available

    # --- rosters and need ------------------------------------------------------------------

    def roster(self, team_slot: int) -> list[int]:
        """This team's players, in the order it drafted them. A copy."""
        self._check_slot(team_slot)
        return list(self._rosters[team_slot])

    def open_dedicated(self, team_slot: int) -> frozenset[str]:
        """This team's unfilled dedicated starter positions — its need (`app.draft.needs`)."""
        self._check_slot(team_slot)
        return self._fills[team_slot].open_dedicated

    def fills_need(self, team_slot: int, player_id: int) -> bool:
        """Would this player cover a dedicated starter slot this team still has open?"""
        self._check_slot(team_slot)
        return self._fills[team_slot].fills_need(self.positions(player_id))

    # --- advancing -------------------------------------------------------------------------

    def apply_pick(self, player_id: int, *, team_slot: int | None = None) -> Pick:
        """Draft this player for the team on the clock, and return the pick it made.

        `team_slot` is an optional assertion, not a choice: pass it and the pick is refused
        unless it matches whoever the snake says is picking. That is what a manual entry wants
        — "team 7 took Jokic" should fail loudly if the room thinks team 6 is up, rather than
        recording a pick under the wrong seat and desynchronising every later one.
        """
        if self.is_complete:
            raise ValueError(
                f"the draft is complete ({self._config.total_picks} picks); nothing left to draft"
            )
        if player_id not in self._universe:
            raise ValueError(
                f"player {player_id} is not in this draft's universe "
                f"({len(self._universe)} players); nobody ranks him"
            )
        if player_id not in self._available:
            raise ValueError(f"player {player_id} has already been drafted")

        number = self.next_pick_number
        assert number is not None  # guarded by is_complete above
        clock = self._config.pick_slot(number)
        if team_slot is not None and team_slot != clock:
            raise ValueError(f"pick {number} belongs to team slot {clock}, not {team_slot}")

        self._available.discard(player_id)
        self._rosters[clock].append(player_id)
        self._fills[clock].add(self.positions(player_id))
        pick = Pick(pick_number=number, team_slot=clock, player_id=player_id)
        self._picks.append(pick)
        return pick

    def pass_pick(self) -> Pick:
        """Consume the pick on the clock without taking anyone (see the module docstring).

        Simulation-only. The board is untouched, no roster grows, and no need changes — the
        clock simply moves on. This is how availability is projected past my own seat.
        """
        if self.is_complete:
            raise ValueError(
                f"the draft is complete ({self._config.total_picks} picks); nothing left to pass"
            )
        number = self.next_pick_number
        assert number is not None  # guarded by is_complete above
        pick = Pick(pick_number=number, team_slot=self._config.pick_slot(number), player_id=None)
        self._picks.append(pick)
        return pick

    # --- forking ---------------------------------------------------------------------------

    def copy(self) -> "DraftState":
        """An independent state at the same point — what each Monte-Carlo iteration forks from.

        The universe and the position map are shared (both immutable in practice and read-only
        here); everything a pick touches is copied, so a simulated draft can run to its end
        without the committed state noticing.
        """
        clone = DraftState.__new__(DraftState)
        clone._config = self._config
        clone._universe = self._universe
        clone._positions = self._positions
        clone._picks = list(self._picks)
        clone._available = set(self._available)
        clone._rosters = {slot: list(roster) for slot, roster in self._rosters.items()}
        clone._fills = {slot: fill.copy() for slot, fill in self._fills.items()}
        return clone

    # --- internals -------------------------------------------------------------------------

    def _check_slot(self, team_slot: int) -> None:
        if team_slot not in self._rosters:
            raise ValueError(f"team_slot {team_slot} is outside 1..{self._config.team_count}")

    def __repr__(self) -> str:
        return (
            f"DraftState(picks={len(self._picks)}/{self._config.total_picks}, "
            f"on_the_clock={self.on_the_clock}, available={len(self._available)})"
        )
