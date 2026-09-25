"""How the other nine teams draft: best available, softened, with a nudge towards need.

The field drafts off the equal-weight CONSENSUS of the selected sources, never off my master
board. That is the point of the whole exercise — availability is a claim about what the room
will do, and the room has not read my rankings. `app.draft.field` builds the mapping; every
function here takes it as `field_ranks` (player id -> 1-based place, lower is better) and has
no idea where it came from.

THE PICK, in three steps:

1. **Shortlist.** The `top_k` best-ranked players still on the board. Nobody in a real draft
   is choosing between the consensus #4 and the consensus #90, and letting them would put a
   long tail of absurd reaches into every simulation.
2. **Weight.** ``weight = exp(-(rank - best_rank) / temperature) * (need_mult if he fills an
   open dedicated starter slot else 1.0)``. The subtraction of `best_rank` is bookkeeping, not
   a rule: it cancels in the normalisation, and it keeps the exponential from underflowing on
   a deep board. So the base weight is ``exp(-rank / temperature)`` as specified — a player
   ranked `temperature` places better is `e` times likelier to go.
3. **Sample** one of them from the normalised weights, using an injected `random.Random`.

NO HARD POSITIONAL RULES, deliberately. With 13 bench slots and 20 rounds every team fills a
legal roster whatever it takes, so the only legality the engine enforces is "one player, once"
and "20 picks". Need enters as a 1.5x thumb on the scale and nothing more (`app.draft.needs`).

SOFT, not deterministic, because the number this feeds is a probability. A field that drafted
the consensus straight down would make every availability 0% or 100% and tell me nothing about
the player I am actually undecided about; the temperature is what turns "he'll probably be
gone" into 31%.

Pure: stdlib `random` and `math`, hand-built dicts, no numpy.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from math import exp
from random import Random

from app.draft.config import (
    DEFAULT_AUTOPICK_NEED_MULT,
    DEFAULT_AUTOPICK_TEMPERATURE,
    DEFAULT_AUTOPICK_TOP_K,
)
from app.draft.state import DraftState, Pick


@dataclass(frozen=True)
class FieldBoard:
    """A field ranking with its order precomputed — the shortlist step, made cheap.

    Sorting a thousand-player mapping is nothing; sorting it once per simulated pick, fifteen
    thousand times, is the difference between a Monte Carlo that answers in a moment and one
    that doesn't. Build it once and hand it down; `of()` makes passing either shape free.
    """

    # Player ids, best rank first. Ties break on player id so a hand-built mapping with
    # repeated ranks still produces one stable order rather than whatever the dict felt like.
    order: tuple[int, ...]
    ranks: Mapping[int, int]

    @classmethod
    def of(cls, field_ranks: "Mapping[int, int] | FieldBoard") -> "FieldBoard":
        if isinstance(field_ranks, FieldBoard):
            return field_ranks
        order = tuple(
            player_id
            for player_id, _ in sorted(field_ranks.items(), key=lambda item: (item[1], item[0]))
        )
        return cls(order=order, ranks=dict(field_ranks))


def candidates(
    state: DraftState,
    field_ranks: Mapping[int, int] | FieldBoard,
    *,
    top_k: int = DEFAULT_AUTOPICK_TOP_K,
) -> list[int]:
    """The `top_k` best-ranked players still on the board, best first.

    Fewer than `top_k` near the end of a draft, and empty once the field has nobody ranked
    left to take — which is a real state, not an error (see `auto_pick`).
    """
    if top_k < 1:
        raise ValueError(f"DRAFT_AUTOPICK_TOPK ({top_k}) must be >= 1")
    board = FieldBoard.of(field_ranks)
    shortlist: list[int] = []
    for player_id in board.order:
        if state.is_available(player_id):
            shortlist.append(player_id)
            if len(shortlist) == top_k:
                break
    return shortlist


def pick_weights(
    state: DraftState,
    team_slot: int,
    shortlist: list[int],
    board: FieldBoard,
    *,
    temperature: float = DEFAULT_AUTOPICK_TEMPERATURE,
    need_mult: float = DEFAULT_AUTOPICK_NEED_MULT,
) -> list[float]:
    """The unnormalised weight of each shortlisted player for this team. Step 2 above.

    Exposed rather than inlined because it is the formulation worth asserting directly: a
    seeded draw tells you the tilt is there, the arithmetic tells you it is 1.5x.
    """
    if temperature <= 0:
        raise ValueError(f"DRAFT_AUTOPICK_TEMPERATURE ({temperature}) must be > 0")
    if need_mult <= 0:
        raise ValueError(f"DRAFT_AUTOPICK_NEED_MULT ({need_mult}) must be > 0")
    if not shortlist:
        return []

    best = min(board.ranks[player_id] for player_id in shortlist)
    return [
        exp(-(board.ranks[player_id] - best) / temperature)
        * (need_mult if state.fills_need(team_slot, player_id) else 1.0)
        for player_id in shortlist
    ]


def auto_pick(
    state: DraftState,
    field_ranks: Mapping[int, int] | FieldBoard,
    rng: Random,
    *,
    top_k: int = DEFAULT_AUTOPICK_TOP_K,
    temperature: float = DEFAULT_AUTOPICK_TEMPERATURE,
    need_mult: float = DEFAULT_AUTOPICK_NEED_MULT,
) -> int | None:
    """Who the team on the clock takes. Does NOT apply the pick — the caller decides that.

    Returns None when there is nobody to take: the draft is complete, or every ranked player
    is gone. A no-op rather than a raise, because both are ordinary ends to a projection and
    the caller's loop should stop, not crash. (A simulation that runs out of ranked players
    has reached the bottom of the consensus board, which in a 200-pick draft off a 1,000-name
    board does not happen — but a hand-built test universe of a dozen players gets there in
    two rounds, and so would a very short imported list.)

    Deterministic given `rng`: the same seeded `Random` and the same state always take the
    same player, which is what makes an availability number reproducible.
    """
    team_slot = state.on_the_clock
    if team_slot is None:
        return None
    board = FieldBoard.of(field_ranks)
    shortlist = candidates(state, board, top_k=top_k)
    if not shortlist:
        return None
    weights = pick_weights(
        state, team_slot, shortlist, board, temperature=temperature, need_mult=need_mult
    )
    return rng.choices(shortlist, weights=weights, k=1)[0]


def simulate_opponents_until(
    state: DraftState,
    field_ranks: Mapping[int, int] | FieldBoard,
    rng: Random,
    *,
    stop_slot: int | None = None,
    stop: Callable[[DraftState], bool] | None = None,
    top_k: int = DEFAULT_AUTOPICK_TOP_K,
    temperature: float = DEFAULT_AUTOPICK_TEMPERATURE,
    need_mult: float = DEFAULT_AUTOPICK_NEED_MULT,
) -> list[Pick]:
    """Run the room forward until my clock, and return the picks it made.

    **Mutates `state`.** This is simulation mode advancing the board to my seat: the picks it
    makes are real picks on the state it was handed. Fork with `state.copy()` first if what
    you wanted was a hypothetical.

    Stops before the pick belonging to `stop_slot` (my seat by default) — it never picks for
    me, here or in `simulate_availability`, for the reason in `app.draft.state`. Also stops
    when the draft ends, when `stop(state)` says so, or when the field has nobody ranked left.
    """
    if stop_slot is None:
        stop_slot = state.config.my_slot
    board = FieldBoard.of(field_ranks)
    made: list[Pick] = []
    while not state.is_complete:
        if state.on_the_clock == stop_slot:
            break
        if stop is not None and stop(state):
            break
        player_id = auto_pick(
            state, board, rng, top_k=top_k, temperature=temperature, need_mult=need_mult
        )
        if player_id is None:
            break
        made.append(state.apply_pick(player_id))
    return made
