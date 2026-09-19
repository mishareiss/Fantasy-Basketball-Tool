"""Tiers on OUR board: contiguous rank bands, seeded from value gaps and then owned.

`app.valuation.tiers` cuts a list of VALUES into tiers. This module is what happens when those
tiers have to sit on a board whose order is a person's decision rather than the values' own —
and the difference between the two is the whole design here.

**A TIER IS A RANK BAND, NOT A SET OF PLAYERS.** What is stored is a list of CUT-RANKS: "a new
tier starts at rank 12". Nothing here is keyed by player id, and that is deliberate. When a
player is dragged from 14 up to 3 he becomes tier 1 because rank 3 is in the tier-1 band — no
tier edit, no reconcile, no second write. Tiers reflow with the order the way a page reflows
with its text. The alternative (tier stored per player) has to answer "what tier is he in now"
after every drag, and every answer it can give is wrong for some drag.

The stored list is the list of TIER START RANKS and it always begins with 1, because tier 1
starts at the top of the board. So `[1, 4, 12]` is three tiers: 1-3, 4-11, 12-N. A scope that
is one undivided tier stores `[1]` — which is also how "this scope has been seeded" is
recorded, without a second column that means nothing else.

**SEEDING is a read of the values; everything after is a read of the stored cuts.** The first
time a scope is asked for, its cuts are derived by running `assign_tiers` over the board's
dynasty values IN BOARD ORDER, persisted, and from then on they are Misha's. That mirrors
`app.ranking.master`'s seed-on-read exactly, for the same reason: a board that came back
untiered until someone pressed a button would come back untiered.

Two rules make "in board order" safe, because a hand-worked board is NOT sorted by value:

* **The descending envelope.** `assign_tiers` requires descending values, and it should: a gap
  is only meaningful between a player and the one below him. So what gets tiered is the
  board's running MINIMUM — how far the floor has fallen by this rank. Where Misha has
  promoted someone above better-valued players the envelope is flat and no tier opens there,
  which is right: he moved a player, he did not claim a cliff.
* **The valueless carry.** A rank-only import has no projection and therefore no dynasty value.
  He carries the running value rather than reading as a drop to zero, so he lands in the tier
  of the players around him instead of opening one of his own and being stranded in it. A
  player we cannot price is not a player we have priced at nothing.

**SCOPES.** 'overall' is the whole ranked board. Each of PG/SG/SF/PF/C is that position's
sub-order — the ranked board filtered to players listed at it, in board order — and its cut
ranks count POSITIONS WITHIN THAT SUB-ORDER, not overall ranks. The eighth-best point guard is
at PG-rank 8 whatever his overall rank is, which is the only way "tier 2 point guards" means
anything. A multi-position player is in every sub-order he is listed in and therefore has a
tier in each.

Pure and DB-free above the `--- storage ---` line, so the arithmetic is testable without a
session; the functions below it are a thin CRUD over `MasterTierBreak`.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import MasterTierBreak
from app.db.models.master_tier import SCOPE_OVERALL, TIER_SCOPES
from app.valuation import TierParams, assign_tiers

# The five positions ESPN lists our players at, which are also the five per-position scopes.
# Defined here rather than on the model because this is the module that gives them meaning:
# a position is a scope, i.e. a sub-order of the board that has tiers of its own.
POSITIONS = tuple(scope for scope in TIER_SCOPES if scope != SCOPE_OVERALL)

# Below this many players a scope is not tiered at all: "tier 1 of 1" is not information, and
# a break needs something on both sides of it. Those players get a null position tier, which
# reads as "not applicable" rather than as "bottom tier".
MIN_SCOPE_SIZE = 2


class UnknownScope(ValueError):
    """A scope outside TIER_SCOPES. Routes turn this into a 422."""

    def __init__(self, scope: str) -> None:
        super().__init__(
            f"Unknown scope {scope!r}; supported: "
            + ", ".join(repr(name) for name in TIER_SCOPES)
            + "."
        )
        self.scope = scope


class BadCutRanks(ValueError):
    """Cut ranks that can't describe a set of bands over this board. Routes 422 it.

    Carries what was wrong in the message rather than just saying so, because the client is a
    drag-drop divider: "cut rank 940 is past the end of a 312-player board" tells it to
    refresh, and "invalid" doesn't.
    """


# --- the arithmetic (pure) -------------------------------------------------------------------


def require_scope(scope: str) -> str:
    """The scope, normalized, or `UnknownScope`. 'pg' is a point guard; 'forward' is a typo."""
    wanted = scope.strip()
    normalized = SCOPE_OVERALL if wanted.lower() == SCOPE_OVERALL else wanted.upper()
    if normalized not in TIER_SCOPES:
        raise UnknownScope(scope)
    return normalized


def value_envelope(values: Sequence[float | None]) -> list[float]:
    """The board's running minimum, with valueless players carrying it — what gets tiered.

    Two problems, one answer. `assign_tiers` needs a descending sequence and a hand-ordered
    board isn't one; and a player with no value would otherwise read as a drop to nothing.
    Taking the running minimum solves the first (the result is non-increasing by construction)
    and carrying it across a `None` solves the second (his gap is exactly 0, so no break can
    open on him and he lands in the tier of the players around him).

    A valueless player ABOVE the first priced one carries backwards instead — from the first
    value there is, because there is nothing above him to carry down. Same effect: a flat top,
    no break, no singleton.
    """
    priced = [value for value in values if value is not None]
    if not priced:
        # Nobody on this board can be priced. One flat sequence, so one tier — honest, and it
        # keeps every caller below from needing an empty-case of its own.
        return [0.0] * len(values)

    running = priced[0]
    envelope = []
    for value in values:
        if value is not None:
            running = min(running, value)
        envelope.append(running)
    return envelope


def seed_cuts(values: Sequence[float | None], params: TierParams) -> list[int]:
    """Derive a scope's tier start ranks from its values, in board order. Always starts with 1.

    One call into `assign_tiers` — the same gap-cluster tierer `GET /players/board` cuts the
    value board with, at the same `TIER_*` dials — over the envelope above. A cut is recorded
    wherever the tier number it hands back goes up.

    Players past `TierParams.pool` come back untiered from `assign_tiers` (boundaries among
    players nobody will draft are noise). Here they simply stay in the last band: on a board of
    bands, "below the tiered pool" is the bottom tier, and a null tier on a row that has a rank
    would be a hole in the middle of a list of dividers.
    """
    if not values:
        return []

    assignments = assign_tiers(value_envelope(values), params)
    cuts = [1]
    previous = assignments[0]
    for index, tier in enumerate(assignments[1:], start=2):
        if tier is not None and previous is not None and tier > previous:
            cuts.append(index)
        if tier is not None:
            previous = tier
    return cuts


def validate_cuts(cut_ranks: Sequence[int], count: int) -> tuple[int, ...]:
    """Check a submitted set of dividers describes bands over a `count`-player board.

    Strict on purpose, and each rule is one way a divider drag can go wrong:
    sorted and unique (two dividers in one slot is not two tiers), inside 1..N (a divider past
    the end of the board bands nobody), and starting at 1 (tier 1 starts at the top; a list
    that omits it is describing a board whose first tier is tier 2).
    """
    if count <= 0:
        if list(cut_ranks):
            raise BadCutRanks(
                "This scope has no ranked players, so there is nothing to cut into tiers; "
                "send an empty list."
            )
        return ()

    ranks = list(cut_ranks)
    if not ranks or ranks[0] != 1:
        raise BadCutRanks(
            "`cut_ranks` is the rank each tier STARTS at, so it must begin with 1 — tier 1 "
            f"starts at the top of the board. Got {ranks}."
        )
    if any(later <= earlier for earlier, later in zip(ranks, ranks[1:], strict=False)):
        raise BadCutRanks(
            f"`cut_ranks` must be strictly increasing (sorted, no duplicates). Got {ranks}."
        )
    outside = [rank for rank in ranks if rank < 1 or rank > count]
    if outside:
        raise BadCutRanks(
            f"cut ranks {outside} are outside this scope's board of {count} players "
            "(1.." + str(count) + "). GET /master/board for the current size."
        )
    return tuple(ranks)


def tiers_for(cut_ranks: Sequence[int], count: int) -> list[int]:
    """Tier number per rank, positionally: `[0]` is rank 1's tier. 1 is the top tier.

    The whole cut-rank -> tier mapping, and it is one sentence: a player's tier is how many
    cuts are at or above his rank. That is what makes the bands reflow for free — nothing is
    keyed by who he is, only by where he is.
    """
    if count <= 0:
        return []
    cuts = sorted(set(cut_ranks)) or [1]
    tiers = []
    number = 0
    position = 0
    for rank in range(1, count + 1):
        while position < len(cuts) and cuts[position] <= rank:
            number += 1
            position += 1
        # A board whose first cut is somehow past rank 1 still has a tier 1 at the top; this
        # can only be reached by a hand-edited database, and 0 is not a tier.
        tiers.append(max(number, 1))
    return tiers


def scope_orders(
    ranked: Sequence[tuple[int, Sequence[str]]],
) -> dict[str, list[int]]:
    """Split the ranked board into every scope's sub-order: scope -> player ids, in board order.

    Takes `(player_id, positions)` in board order, which is all a scope is a function of.
    'overall' is the board itself; each position is the board filtered to players listed at it.
    A player listed at two positions is in two sub-orders and gets a tier in each.
    """
    orders: dict[str, list[int]] = {scope: [] for scope in TIER_SCOPES}
    for player_id, positions in ranked:
        orders[SCOPE_OVERALL].append(player_id)
        for position in positions or ():
            if position in orders:
                orders[position].append(player_id)
    return orders


# --- storage -----------------------------------------------------------------------------------


def load_cuts(db: Session) -> dict[str, list[int]]:
    """Every stored scope's cut ranks, ascending. Scopes with nothing stored are absent.

    Absent is the signal `ensure_cuts` seeds on, and it is unambiguous because a seeded scope
    always stores at least the leading 1.
    """
    cuts: dict[str, list[int]] = {}
    for scope, cut_rank in db.execute(
        select(MasterTierBreak.scope, MasterTierBreak.cut_rank).order_by(
            MasterTierBreak.scope, MasterTierBreak.cut_rank
        )
    ):
        cuts.setdefault(scope, []).append(cut_rank)
    return cuts


def save_cuts(db: Session, scope: str, cut_ranks: Iterable[int]) -> list[int]:
    """Replace a scope's cuts wholesale. Flushes; the caller owns the commit.

    Wholesale rather than a diff because that is what the client has: a divider drag produces
    the new set of dividers, not a delta, and reconciling a delta against a board that has
    since reflowed is a race with no upside.
    """
    db.execute(delete(MasterTierBreak).where(MasterTierBreak.scope == scope))
    ranks = sorted(set(cut_ranks))
    for cut_rank in ranks:
        db.add(MasterTierBreak(scope=scope, cut_rank=cut_rank))
    db.flush()
    return ranks


def clear_cuts(db: Session, scope: str) -> None:
    """Forget a scope's cuts, so the next read seeds it from the values again."""
    db.execute(delete(MasterTierBreak).where(MasterTierBreak.scope == scope))
    db.flush()


def ensure_cuts(
    db: Session,
    orders: Mapping[str, Sequence[int]],
    values: Callable[[], Mapping[int, float]],
    params: TierParams,
) -> dict[str, list[int]]:
    """Seed-on-read: give every scope cuts, deriving and PERSISTING the ones that have none.

    The same shape as `app.ranking.master.reconcile`, and for the same reason — the board has
    to come back tiered on the first look, and the tiers have to be a stable thing Misha then
    edits rather than something recomputed under him. Once a scope is stored this function
    never touches it again: a reorder reflows the bands, it does not re-derive them.

    `values` is a CALLABLE, and that is the one piece of laziness worth the indirection here:
    pricing the pool means valuing a thousand players through the age curve, and the steady
    state of this function is "every scope is already stored, seed nothing". Called at most
    once, only when some scope actually has to be cut.

    Flushes, never commits. A scope whose sub-order is too short to tier (`MIN_SCOPE_SIZE`) is
    left unstored and absent from the result, which is what makes its players' position tier
    null instead of a meaningless 1.
    """
    stored = load_cuts(db)
    needed = [
        scope
        for scope in TIER_SCOPES
        if scope not in stored and len(orders.get(scope, ())) >= MIN_SCOPE_SIZE
    ]
    if not needed:
        return stored

    priced = values()
    for scope in needed:
        cuts = seed_cuts([priced.get(player_id) for player_id in orders[scope]], params)
        if cuts:
            stored[scope] = save_cuts(db, scope, cuts)
    return stored
