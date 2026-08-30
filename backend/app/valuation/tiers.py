"""Auto-tiering by score-gap clustering: where the board stops being a list and becomes a plan.

A ranked board says who is better. A TIERED board says who is *interchangeable* — "draft
anyone still on the board in this tier" — which is the only form the information is usable in
with 90 seconds on the clock (FEATURE_SPEC 6).

The rule is deliberately one sentence: walking the ranked values downward, a new tier opens
wherever the drop to the next player is bigger than `gap_multiple` times the TYPICAL drop.

Typical is the **median** gap, not the mean, and that choice is the whole reason this works.
The top of a dynasty board has two or three enormous cliffs in it; a mean is dragged upward by
exactly those, the threshold inflates, and the ordinary-but-real breaks further down get
swallowed into one 40-man blob. The median doesn't notice them.

Two guards keep the output draftable rather than merely correct:

- `min_size` — no singleton tiers. A tier of one is a ranking, not a tier, so a break that
  would strand a player alone is dropped... unless the gap that opened it is genuinely huge
  (`HUGE_GAP_FACTOR` times the threshold), which is how a true outlier at the top of the board
  is allowed to stand by himself.
- `max_tiers` — a cap. Twenty-five tiers is a list again. When the breaks overflow the cap the
  LEAST significant ones are the ones that go, so what survives is the biggest cliffs.

Tiering runs over the top `pool` players, not the whole ~1,095-player pool. Boundaries among
players nobody will draft are noise, and letting that noise into the median would move the
boundaries that matter. Everyone below the pool gets tier `None`: untiered, not tier 99.

Pure and deterministic — no database, no settings import, no clock. The parameters arrive as
`TierParams` (built by `Settings.tier_params()`), and the same values in always give the same
tiers out.
"""

from bisect import bisect_left, insort
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from statistics import fmean, median

# How much bigger than the ordinary break threshold a gap has to be before it is allowed to
# strand a player in a tier of his own — three times it, so at the default 2.0x multiple a
# singleton needs a drop six times the typical one. Derived from the threshold rather than
# being a fifth env var: it means "even by the standards of a break, this one is enormous",
# which is a statement about the other breaks, not a number to tune independently.
#
# Three, not two, because the top of a dynasty board is all cliff: at 2.0 the first half-dozen
# names each cleared the bar and the board opened with a run of five tiers of one, which is a
# ranking with extra lines drawn on it. At 3.0 only the genuinely separate names stand alone.
HUGE_GAP_FACTOR = 3.0

# Gaps are reported, not ranked by, so they are rounded to kill float subtraction noise —
# 0.30000000000000004 in a response reads as a bug. Decisions are made on the raw values.
PRECISION = 4


@dataclass(frozen=True)
class TierParams:
    """The four tunables, validated once at construction.

    Frozen for the same reason `DynastyCurve` is: one board must be cut by one set of numbers.
    """

    # A break is a gap strictly bigger than this multiple of the median gap.
    gap_multiple: float
    # Smallest tier the min-size guard will allow (bar a genuinely huge gap).
    min_size: int
    # Hard cap on how many tiers a board may have.
    max_tiers: int
    # How many top-ranked players get tiered at all.
    pool: int

    def __post_init__(self) -> None:
        """Refuse parameters that can't mean anything, naming the env var that's wrong.

        These come from the environment, so without this the failure mode is a board silently
        tiered by nonsense — a zero multiple that makes every single gap a break, or a
        negative pool that tiers nobody.
        """
        if self.gap_multiple <= 0:
            raise ValueError(f"TIER_GAP_MULTIPLE ({self.gap_multiple}) must be > 0")
        if self.min_size < 1:
            raise ValueError(f"TIER_MIN_SIZE ({self.min_size}) must be >= 1")
        if self.max_tiers < 1:
            raise ValueError(f"TIER_MAX ({self.max_tiers}) must be >= 1")
        if self.pool < 1:
            raise ValueError(f"TIER_POOL ({self.pool}) must be >= 1")


@dataclass(frozen=True)
class Tier:
    """One tier: where it starts, how big it is, and the cliff that opened it."""

    # 1 is the top tier.
    tier: int
    size: int
    # The values of its first and last player — the band, in the horizon's own units.
    value_high: float
    value_low: float
    # 0-based index into the tiered pool of its first player; his board rank is this + 1.
    start_index: int
    # The gap down from the tier above that opened this one. None for tier 1, which is opened
    # by the top of the board rather than by a break.
    gap: float | None
    # That gap as a multiple of the typical (median) gap — how significant the break is. This
    # is the number the max_tiers cap merges by, and the one worth eyeballing.
    gap_ratio: float | None


@dataclass(frozen=True)
class Tiering:
    """The whole tier structure over one ranked pool, plus what produced it."""

    params: TierParams
    # How many players were actually tiered: min(pool, len(values)).
    pool_size: int
    # The median gap across the tiered pool, and gap_multiple * it. Reported because "why is
    # this a break and that one isn't" is the first question anyone asks of a tiered board.
    typical_gap: float
    threshold: float
    tiers: list[Tier]
    # One entry per input value, positionally: the tier number, or None below the pool.
    assignments: list[int | None]

    @property
    def breaks(self) -> list[Tier]:
        """The tiers that a gap opened — every tier but the first."""
        return [tier for tier in self.tiers if tier.gap is not None]


def assign_tiers(values: Sequence[float], params: TierParams) -> list[int | None]:
    """Tier number per player, positionally, over descending-sorted `values`. 1 is the top.

    `None` means "below the tiered pool" — see `tier_structure`, which this is the short
    answer to.
    """
    return tier_structure(values, params).assignments


def tier_structure(values: Sequence[float], params: TierParams) -> Tiering:
    """Cut `values` into tiers, keeping the breaks and the numbers behind them.

    `values` must be sorted DESCENDING — it is the board's own ordering under the selected
    horizon, and tiering a differently-ordered list would produce tiers the board can't draw.
    """
    _require_descending(values)

    pool = list(values[: params.pool])
    if not pool:
        return Tiering(
            params=params,
            pool_size=0,
            typical_gap=0.0,
            threshold=0.0,
            tiers=[],
            assignments=[None] * len(values),
        )

    gaps = [pool[index] - pool[index + 1] for index in range(len(pool) - 1)]
    typical = _typical_gap(gaps)
    threshold = params.gap_multiple * typical

    breaks = _select_breaks(gaps, threshold=threshold, params=params, pool_size=len(pool))
    tiers = _build_tiers(pool, gaps, breaks, typical=typical)

    assignments: list[int | None] = [None] * len(values)
    for tier in tiers:
        for index in range(tier.start_index, tier.start_index + tier.size):
            assignments[index] = tier.tier

    return Tiering(
        params=params,
        pool_size=len(pool),
        typical_gap=round(typical, PRECISION),
        threshold=round(threshold, PRECISION),
        tiers=tiers,
        assignments=assignments,
    )


def _require_descending(values: Sequence[float]) -> None:
    """The contract, checked. A pool of ~150 makes this free, and the bug it catches isn't."""
    for index in range(len(values) - 1):
        if values[index] < values[index + 1]:
            raise ValueError(
                "values must be sorted descending to be tiered; "
                f"index {index} ({values[index]}) < index {index + 1} ({values[index + 1]})"
            )


def _typical_gap(gaps: Sequence[float]) -> float:
    """The median gap — the yardstick every break is measured against.

    Falls back to the mean only when the median is zero, which happens when more than half the
    pool is tied on the same value. A zero yardstick would make every non-zero drop a break;
    the mean at least still knows how far apart the players who *do* differ are. When both are
    zero every value is identical, and a board with no drops in it has no tiers to find.
    """
    if not gaps:
        return 0.0
    typical = median(gaps)
    if typical > 0:
        return typical
    return fmean(gaps)


def _select_breaks(
    gaps: Sequence[float], *, threshold: float, params: TierParams, pool_size: int
) -> list[int]:
    """Which gaps become tier boundaries, most significant first. Returns gap indices, sorted.

    Gap `i` sits between pool positions `i` and `i + 1`, so accepting it opens a tier at
    `i + 1`.

    Taking the biggest gaps first is what makes both guards mean the right thing. The
    `max_tiers` cap simply stops accepting, so the breaks that get merged away are the least
    significant ones. And the `min_size` guard is judged against the breaks already accepted,
    so a small tier is only ever rejected in favour of bigger cliffs, never the reverse.
    """
    huge = threshold * HUGE_GAP_FACTOR
    candidates = sorted(
        (index for index, gap in enumerate(gaps) if gap > threshold),
        # Biggest gap first; ties by position, so the result never depends on sort stability.
        key=lambda index: (-gaps[index], index),
    )

    accepted: list[int] = []
    for index in candidates:
        # Every accepted break adds a tier, and the first tier is free.
        if len(accepted) >= params.max_tiers - 1:
            break

        start = bisect_left(accepted, index)
        previous = accepted[start - 1] + 1 if start else 0
        following = accepted[start] + 1 if start < len(accepted) else pool_size
        above = (index + 1) - previous
        below = following - (index + 1)
        if min(above, below) < params.min_size and gaps[index] <= huge:
            continue

        insort(accepted, index)

    return accepted


def _build_tiers(
    pool: Sequence[float], gaps: Sequence[float], breaks: Sequence[int], *, typical: float
) -> list[Tier]:
    """Turn accepted break positions into the tiers they cut the pool into."""
    boundaries = [0, *(index + 1 for index in breaks), len(pool)]
    tiers = []
    for number, (start, end) in enumerate(pairwise(boundaries), start=1):
        gap = gaps[start - 1] if start > 0 else None
        tiers.append(
            Tier(
                tier=number,
                size=end - start,
                value_high=pool[start],
                value_low=pool[end - 1],
                start_index=start,
                gap=round(gap, PRECISION) if gap is not None else None,
                gap_ratio=round(gap / typical, PRECISION) if gap is not None and typical else None,
            )
        )
    return tiers
