"""The draft engine: what the room will do, and therefore who is still there when I pick.

The board tells me who is better. This package answers the question that actually decides a
startup draft — *who will still be on it seventeen picks from now* — and it answers it by
drafting the other nine teams, a thousand times, off the equal-weight consensus of whichever
sources I say the room reads (FEATURE_SPEC: the round-by-round plan).

Four pure modules and one adapter:

* `config` — the shape: 10 teams, 20 rounds, my seat, the roster's slots, and the snake
  flattened once so `my_pick_numbers` is [2, 19, 22, 39, 42, ...] and not a modulo puzzle.
* `needs` — the soft definition of need: a team's OPEN DEDICATED starter slots. UT and the
  bench are positionless, so a team with its starting five set needs nobody.
* `state` — the picks made, the clock, the board, and the two ways to advance: commit a real
  selection, or (simulation only) step over my own seat without taking anyone.
* `autopick` — how the field drafts: the top-K available by consensus rank, weighted
  `exp(-rank / T)` with a 1.5x nudge towards need, sampled from a seeded `Random`.
* `availability` — the Monte Carlo over all of the above: player -> {my pick number -> % still
  there}, deterministic under a seed.
* `field` — the only module that touches a `Session`, and a thin one: `load_catalog` ->
  `catalog.select` -> `consensus_positions`, reusing the consensus path rather than growing a
  parallel ranking.

The pure four import nothing from `app.db`, `app.api` or SQLAlchemy, which is what lets a
whole simulated draft be asserted on a dozen hand-written players and four teams.

THE MODELLING CHOICE worth knowing before reading a number off this: the simulation never
picks for me. Availability at my next pick is therefore exact given the committed state, and
availability at a later pick is computed as though I take nobody in between. See
`app.draft.availability` for why that is the right-signed simplification.

Still ahead, and deliberately not here: persistence of a live draft, the endpoints over it,
and the round-by-round target list built on top of these numbers (Task 20), plus the draft
room itself (Task 21). Nothing in this package knows what HTTP is.
"""

from app.draft.autopick import (
    FieldBoard,
    auto_pick,
    candidates,
    pick_weights,
    simulate_opponents_until,
)
from app.draft.availability import simulate_availability
from app.draft.config import (
    BENCH_SLOT,
    DEDICATED_POSITIONS,
    DEFAULT_AUTOPICK_NEED_MULT,
    DEFAULT_AUTOPICK_TEMPERATURE,
    DEFAULT_AUTOPICK_TOP_K,
    DEFAULT_ROSTER_SLOTS,
    DEFAULT_SIM_ITERATIONS,
    DEFAULT_SIM_SEED,
    UTILITY_SLOT,
    DraftConfig,
)
from app.draft.field import UnknownSources, field_ranks, positions_for
from app.draft.needs import RosterFill, fill_roster, fills_need, normalize_positions
from app.draft.state import DraftState, Pick

__all__ = [
    "BENCH_SLOT",
    "DEDICATED_POSITIONS",
    "DEFAULT_AUTOPICK_NEED_MULT",
    "DEFAULT_AUTOPICK_TEMPERATURE",
    "DEFAULT_AUTOPICK_TOP_K",
    "DEFAULT_ROSTER_SLOTS",
    "DEFAULT_SIM_ITERATIONS",
    "DEFAULT_SIM_SEED",
    "UTILITY_SLOT",
    "DraftConfig",
    "DraftState",
    "FieldBoard",
    "Pick",
    "RosterFill",
    "UnknownSources",
    "auto_pick",
    "candidates",
    "field_ranks",
    "fill_roster",
    "fills_need",
    "normalize_positions",
    "pick_weights",
    "positions_for",
    "simulate_availability",
    "simulate_opponents_until",
]
