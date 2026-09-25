""" "Will he still be there at 19?" — answered by drafting the rest of the room a thousand times.

This is the number the draft plan is built on. Given who has actually been taken, it runs the
field forward (`app.draft.autopick`) to each of my upcoming picks, over and over with a
different seeded roll each time, and reports the fraction of those drafts in which each player
was still on the board when that pick came up.

THREE THINGS IT DELIBERATELY DOES, each of which changes how the output should be read:

* **It never picks for me.** At every pick number belonging to my seat the board is left
  untouched (`DraftState.pass_pick`). So the answer at my NEXT pick is exact given the
  committed state — nothing of mine intervenes — while the answer at a LATER pick is computed
  as if I take nobody in between. That is a real simplification, and it is the right-signed
  one: it can only overstate who survives to pick 22, and by exactly the players I was going
  to take at 19 anyway. The alternative — simulating a me who grabs the consensus best
  available — would delete players from my own availability numbers for the crime of my
  wanting them.
* **It measures BEFORE the pick.** "Available at pick 19" means still on the board at the
  moment pick 19 comes up, not after it.
* **It stops at the last target.** Asking about picks 19 and 22 simulates 20 opponent picks,
  not the remaining 180.

DETERMINISM is a product requirement, not a nicety: this is a percentage someone compares
between two refreshes, and iteration `i` always rolls `Random(seed + i)` so the same committed
state gives the same answer twice. Pure — stdlib only.
"""

from collections.abc import Iterable, Mapping, Sequence
from random import Random

from app.draft.autopick import FieldBoard, auto_pick
from app.draft.config import (
    DEFAULT_AUTOPICK_NEED_MULT,
    DEFAULT_AUTOPICK_TEMPERATURE,
    DEFAULT_AUTOPICK_TOP_K,
    DEFAULT_SIM_ITERATIONS,
    DEFAULT_SIM_SEED,
)
from app.draft.state import DraftState


def simulate_availability(
    state: DraftState,
    field_ranks: Mapping[int, int] | FieldBoard,
    my_pick_numbers: Sequence[int],
    *,
    iterations: int = DEFAULT_SIM_ITERATIONS,
    seed: int = DEFAULT_SIM_SEED,
    top_k: int = DEFAULT_AUTOPICK_TOP_K,
    temperature: float = DEFAULT_AUTOPICK_TEMPERATURE,
    need_mult: float = DEFAULT_AUTOPICK_NEED_MULT,
    candidates: Iterable[int] | None = None,
) -> dict[int, dict[int, float]]:
    """player id -> {pick number: chance he is still on the board when that pick comes up}.

    `my_pick_numbers` are the picks to report at — my remaining ones, in practice, though
    nothing here requires that. Target numbers already consumed by the committed state are
    dropped: availability at a pick that has happened is history, not a probability. Pass none
    that remain and every tracked player comes back with an empty mapping.

    `candidates` defaults to everyone still on the board that the field has a rank for, which
    is both bounded and the only set the answer means anything for. Narrow it to a target list
    when that is what is being asked about. A candidate who is already drafted is reported at
    0.0 everywhere rather than omitted — "gone" is an answer.

    Probabilities are in [0, 1] and each player's series is non-increasing across later picks,
    which is not enforced anywhere: it falls out of a player who is gone at pick 19 also being
    gone at pick 22, in every single iteration.
    """
    if iterations < 1:
        raise ValueError(f"DRAFT_SIM_ITERATIONS ({iterations}) must be >= 1")

    config = state.config
    board = FieldBoard.of(field_ranks)

    tracked = (
        {player_id for player_id in state.available if player_id in board.ranks}
        if candidates is None
        else set(candidates)
    )
    # Someone already taken can't come back. Reported, at zero, rather than silently missing.
    gone = {player_id for player_id in tracked if not state.is_available(player_id)}
    live = tracked - gone

    for number in my_pick_numbers:
        if not 1 <= number <= config.total_picks:
            raise ValueError(f"target pick number {number} is outside 1..{config.total_picks}")
    start = state.next_pick_number
    targets = sorted(
        {number for number in my_pick_numbers if start is not None and number >= start}
    )
    if not targets or not tracked:
        return {player_id: {} for player_id in sorted(tracked)}

    # Counted from the unavailable side: the players taken by a given target are at most a
    # couple of hundred, while `live` is the whole board. Same answer, a fraction of the work.
    misses: dict[int, list[int]] = {player_id: [0] * len(targets) for player_id in live}
    last_target = targets[-1]

    for iteration in range(iterations):
        rng = Random(seed + iteration)
        sim = state.copy()
        # Who this projection has taken. Tracked here rather than read off the state because
        # it is the small side of the intersection below, and the loop runs ~15,000 times.
        taken: set[int] = set()
        target_index = 0

        while not sim.is_complete:
            pick_number = sim.next_pick_number
            assert pick_number is not None  # guarded by is_complete
            if pick_number > last_target:
                break

            if targets[target_index] == pick_number:
                for player_id in taken & live:
                    misses[player_id][target_index] += 1
                target_index += 1

            if config.is_my_pick(pick_number):
                # Decision: the sim takes nobody for me. The clock moves, the board doesn't.
                sim.pass_pick()
                continue

            player_id = auto_pick(
                sim, board, rng, top_k=top_k, temperature=temperature, need_mult=need_mult
            )
            if player_id is None:
                # The field has nobody ranked left to take, so the board never changes again:
                # every remaining target sees it exactly as it stands now.
                for remaining in range(target_index, len(targets)):
                    for player_id in taken & live:
                        misses[player_id][remaining] += 1
                break
            sim.apply_pick(player_id)
            taken.add(player_id)

    result: dict[int, dict[int, float]] = {
        player_id: {number: 0.0 for number in targets} for player_id in sorted(gone)
    }
    for player_id in sorted(live):
        counts = misses[player_id]
        result[player_id] = {
            number: 1.0 - counts[index] / iterations for index, number in enumerate(targets)
        }
    return result
