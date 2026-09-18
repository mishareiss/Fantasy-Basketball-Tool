"""Sportsbook odds -> a fair per-game number. Pure arithmetic, no database, no scoring.

A season-long prop is two things at once: a LINE ("Nikola Jokic, 9.5 assists per game") and a
PRICE on each side of it ("over -135 / under +110"). The line alone is the book's midpoint;
the price is how far off the midpoint the book actually thinks the truth is. Both halves have
to be read, and reading only the line throws away the half that is hardest to get anywhere
else.

The model, in four steps, each of which is one function below:

1. **American odds -> implied probability.** `-135` means "risk 135 to win 100", so the break
   even probability is 135/235. `+110` means "risk 100 to win 110", so it is 100/210.
2. **De-vig.** Those two do not sum to 1 — the difference is the book's margin (the overround,
   the "juice"), and it is on both sides at once. Normalising by the total removes it
   proportionally, which is the standard read and the only one that needs no extra assumption
   about which side the book shaded.
3. **Line + a shift.** A prop is a bet on a random quantity, so model the season's per-game
   outcome as normal around some unknown mean and ask where the mean has to be for the market
   to price the over at `p`. That is `mean = line + sigma * PHI_INV(p)`: a fair coin-flip
   line (`p = 0.5`) leaves the line exactly where the book put it, and a shaded one moves the
   value toward the favoured side by however many standard deviations the price implies.
4. **Clamp at zero.** No counting stat is negative, and a wild line plus a wild price could
   otherwise produce one.

Step 3 is the only part with a free parameter, and it is deliberately a DIAL rather than a
constant: `sigma` is how much per-game spread we think a season-long line carries, and nobody
knows it exactly. It lives in `Settings.market_sigma_frac` (MARKET_SIGMA_FRAC in `.env`)
alongside DYNASTY_* and TIER_*, for the same reason those do — calibrating the board should be
an env change and a restart, not a code change. It is scaled by the line itself
(`sigma = frac * max(line, 1.0)`) because a 27-point scorer and a 1.1-block shot-blocker do
not have the same absolute spread; the floor stops a 0.4-steals line from getting a sigma so
small that a -400 price barely moves it.

What the dial does NOT change: an evenly-priced line. `PHI_INV(0.5)` is exactly 0, so
`value == line` for every even, one-sided or missing price at every sigma. That is the
property worth holding on to — entering a bare line with no odds stores the line, full stop.
"""

from statistics import NormalDist

# The de-vigged over probability for a line whose price we don't have. Deliberately exactly
# a half: it makes `fair_value` return the line untouched, which is what "the book published
# 9.5 assists and we have no price" should mean.
EVEN_PROBABILITY = 0.5

# The smallest line sigma is scaled against. Below it, `frac * line` would shrink toward zero
# and a heavily-shaded price on a 0.3-blocks line would be ignored rather than respected.
MIN_LINE_SCALE = 1.0

# `NormalDist.inv_cdf` is undefined at 0 and 1, and a de-vigged probability can land on
# either if a book prices one side at absurd odds. Clamped rather than raising: the answer is
# "as far toward that side as this model goes", not an import that fails.
_PROBABILITY_EPSILON = 1e-6

_STANDARD_NORMAL = NormalDist()


def implied_probability(odds: float | int | None) -> float | None:
    """American odds -> the break-even (vigged) probability they price. None for no price.

    Negative odds are the favourite: `-O` risks O to win 100, so `O / (O + 100)`.
    Positive odds are the underdog: `+O` risks 100 to win O, so `100 / (O + 100)`.

    >>> implied_probability(-110)
    0.5238095238095238
    >>> implied_probability(150)
    0.4
    >>> implied_probability(None) is None
    True
    """
    if odds is None:
        return None
    value = float(odds)
    if value == 0:
        # Not a price anyone writes. Treated as absent rather than as a division by 100.
        return None
    if value < 0:
        magnitude = -value
        return magnitude / (magnitude + 100.0)
    return 100.0 / (value + 100.0)


def devig(over_odds: float | int | None, under_odds: float | int | None) -> float:
    """The fair probability the OVER hits, with the book's margin taken out.

    Both sides priced: the two implied probabilities sum to more than 1, and the excess is the
    book's margin. Normalising by the total splits it proportionally, which is the standard
    de-vig and the one that assumes nothing about which side was shaded.

    One side or neither: `EVEN_PROBABILITY`. A single price can't be de-vigged — there is
    nothing to measure the margin against — and treating `-135` alone as a 57% chance would
    read the book's whole margin as an opinion about the player. Better to say "we have a
    line and no usable price", which is exactly what an even probability means downstream.

    >>> devig(-110, -110)
    0.5
    >>> round(devig(-200, 170), 4)
    0.6382
    >>> devig(-135, None)
    0.5
    """
    probability_over = implied_probability(over_odds)
    probability_under = implied_probability(under_odds)
    if probability_over is None or probability_under is None:
        return EVEN_PROBABILITY

    total = probability_over + probability_under
    if total <= 0:  # pragma: no cover - both probabilities are strictly positive by construction
        return EVEN_PROBABILITY
    return probability_over / total


def dispersion(line: float, sigma_fraction: float) -> float:
    """How much per-game spread we credit this line with — the dial, in one place.

    Proportional to the line so the same fraction means the same thing for a 27-point scorer
    and a 1.1-block rim protector, with `MIN_LINE_SCALE` as the floor so a small line still
    has room to move under a shaded price.
    """
    return sigma_fraction * max(float(line), MIN_LINE_SCALE)


def fair_value(
    line: float,
    over_odds: float | int | None = None,
    under_odds: float | int | None = None,
    *,
    sigma_fraction: float,
) -> float:
    """The per-game number a line and its price together imply. Never negative.

    `line + sigma * PHI_INV(p_over)`, where `p_over` is the de-vigged probability the over
    hits. Even, one-sided or absent odds give `p_over = 0.5`, `PHI_INV(0.5) = 0`, and the line
    stands exactly as published — which is the behaviour to lean on when entering a line by
    hand with no price.

    >>> fair_value(9.5, sigma_fraction=0.25)
    9.5
    >>> round(fair_value(9.5, -110, -110, sigma_fraction=0.25), 4)
    9.5
    >>> fair_value(9.5, 150, -150, sigma_fraction=0.25) < 9.5
    True
    >>> fair_value(9.5, -150, 150, sigma_fraction=0.25) > 9.5
    True
    """
    probability = min(
        max(devig(over_odds, under_odds), _PROBABILITY_EPSILON), 1 - _PROBABILITY_EPSILON
    )
    shift = _STANDARD_NORMAL.inv_cdf(probability)
    # Clamped: a counting stat is never negative, and a wild line under a wild price could be.
    return max(0.0, float(line) + dispersion(line, sigma_fraction) * shift)
