"""Fill in the stats a projection leaves out: DD, TD, OREB, DREB and PF.

ESPN's projected split carries points, rebounds, assists and the rest, but no double-doubles,
triple-doubles, offensive/defensive rebound split or personal fouls. Our league pays DD +10,
TD +30, OREB +0.5 and PF -0.2, so a projection scored without them undervalues every
stat-stuffer on the board. Last season's ACTUAL split does carry them, which gives each player
his own rates to impute from.

Pure: no database, no settings object. The dials arrive as an `ImputeParams` whose defaults
are the `IMPUTE_*` values in `app.config.Settings` (a test asserts the two agree).

What gets imputed, and from what:

* **OREB / DREB** — projected REB x his last-season OREB/REB share; with too little history,
  his position's share across the league. DREB = REB - OREB.
* **PF** — projected MIN x his last-season fouls per minute, else his position's rate.
* **DD / TD** — a blend of his last-season rate and a distribution model of the projected line
  (`double_rates`), weighted by how many games that last season is.

A stat the source DID give is never touched. QD is left alone (absent).
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# The five stats a "double" counts in, and what a double is.
DOUBLE_CATEGORIES = ("PTS", "REB", "AST", "STL", "BLK")
DOUBLE_THRESHOLD = 10.0

# The sd of each category, as a multiple of sqrt(mean): sd = k * sqrt(mean). Fitted 2026-10-16
# on the 2025-26 actuals (fit_sd_k over 451 players with >= 20 GP, each player's ACTUAL
# per-game line against his ACTUAL DD rate, GP-weighted): k = 1.341, weighted RMSE 0.034 DD
# per game against a spread of 0.129 (R^2 0.93). The same k under-reads TDs (0.0030 a game
# modelled vs 0.0052 actual, league-wide): the five categories are positively correlated in
# real box scores, which independence ignores. The blend with his own rate covers most of
# that for anyone with history. Refit with `make impute-calibrate`.
IMPUTE_SD_K = 1.34
# Pseudo-games of the model in the DD/TD blend: w(own rate) = GP / (GP + prior).
IMPUTE_PRIOR_GP = 20.0
# Fewer games than this last season and his own OREB share / foul rate is noise: use the
# position's instead.
IMPUTE_MIN_GP = 15.0

# How each stat was filled, as recorded in `Projection.imputed_stats`.
METHOD_OWN = "own_rate"
METHOD_POSITION = "position_baseline"
METHOD_LEAGUE = "league_baseline"
METHOD_FROM_REB = "reb_minus_oreb"
METHOD_BLEND = "blend"
METHOD_MODEL = "model"


@dataclass(frozen=True)
class ImputeParams:
    sd_k: float = IMPUTE_SD_K
    prior_gp: float = IMPUTE_PRIOR_GP
    min_gp: float = IMPUTE_MIN_GP


DEFAULT_PARAMS = ImputeParams()


@dataclass(frozen=True)
class Rates:
    """A group's OREB share of rebounds and personal fouls per minute."""

    oreb_share: float | None = None
    pf_per_min: float | None = None


@dataclass(frozen=True)
class Baselines:
    """League rates by primary position, plus the whole league for an unknown position."""

    by_position: Mapping[str, Rates] = field(default_factory=dict)
    league: Rates = Rates()

    def for_position(self, position: str | None) -> tuple[Rates, str]:
        """The rates to use for `position`, and which method that is."""
        if position is not None and position in self.by_position:
            return self.by_position[position], METHOD_POSITION
        return self.league, METHOD_LEAGUE


@dataclass(frozen=True)
class LastSeason:
    """What he actually did last season: per-game stats and games played."""

    per_game: Mapping[str, float]
    games: float


@dataclass(frozen=True)
class ImputedLine:
    """A per-game line with the gaps filled, and how each filled stat was arrived at."""

    per_game: dict[str, float]
    imputed: dict[str, str]


def _ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _rates(lines: Iterable[tuple[Mapping[str, float], float]]) -> Rates:
    """GP-weighted rates over (per-game line, games) pairs: sums of season totals, divided."""
    oreb = reb = pf = minutes = 0.0
    for per_game, games in lines:
        if "OREB" in per_game and "REB" in per_game:
            oreb += per_game["OREB"] * games
            reb += per_game["REB"] * games
        if "PF" in per_game and "MIN" in per_game:
            pf += per_game["PF"] * games
            minutes += per_game["MIN"] * games
    return Rates(oreb_share=_ratio(oreb, reb), pf_per_min=_ratio(pf, minutes))


def league_baselines(actuals: Iterable[tuple[str | None, LastSeason]]) -> Baselines:
    """Position and league baselines from every player's last season, GP-weighted.

    `actuals` is (primary position, last season) per player.
    """
    pairs = [(position, (s.per_game, s.games)) for position, s in actuals if s.games > 0]
    positions = {position for position, _ in pairs if position is not None}
    return Baselines(
        by_position={
            position: _rates(line for pos, line in pairs if pos == position)
            for position in sorted(positions)
        },
        league=_rates(line for _, line in pairs),
    )


# --- the DD / TD model ------------------------------------------------------------------------


def _p_at_least(mean: float, threshold: float, sd_k: float) -> float:
    """P(X >= threshold) for X ~ Normal(mean, (sd_k * sqrt(mean))^2). Mean 0 -> sd 0 -> a point."""
    sd = sd_k * math.sqrt(max(mean, 0.0))
    if sd <= 0:
        return 1.0 if mean >= threshold else 0.0
    return 0.5 * math.erfc((threshold - mean) / (sd * math.sqrt(2.0)))


def _at_least_k(probabilities: Iterable[float]) -> list[float]:
    """P(at least j of these independent events happen), for j = 0..n. Exact, by DP."""
    exactly = [1.0]
    for p in probabilities:
        nxt = [0.0] * (len(exactly) + 1)
        for j, mass in enumerate(exactly):
            nxt[j] += mass * (1.0 - p)
            nxt[j + 1] += mass * p
        exactly = nxt
    tail, out = 0.0, [0.0] * len(exactly)
    for j in range(len(exactly) - 1, -1, -1):
        tail += exactly[j]
        out[j] = tail
    return out


def double_rates(per_game: Mapping[str, float], sd_k: float) -> tuple[float, float]:
    """Modelled (DD, TD) per game for a per-game line.

    Each of PTS/REB/AST/STL/BLK is an independent normal with mean = the line's per-game value
    and sd = sd_k * sqrt(mean); a missing category is a mean of 0. DD = P(at least two reach
    10), TD = P(at least three). Exact over the five categories, no sampling.
    """
    probabilities = [
        _p_at_least(max(float(per_game.get(name, 0.0)), 0.0), DOUBLE_THRESHOLD, sd_k)
        for name in DOUBLE_CATEGORIES
    ]
    at_least = _at_least_k(probabilities)
    return _clamp(at_least[2], 0.0, 1.0), _clamp(at_least[3], 0.0, 1.0)


def blend_weight(games_last: float | None, prior_gp: float) -> float:
    """How much of his own last-season rate goes into the blend: GP / (GP + prior)."""
    if not games_last or games_last <= 0:
        return 0.0
    return games_last / (games_last + prior_gp)


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


# --- one line ---------------------------------------------------------------------------------


def impute_line(
    per_game: Mapping[str, float],
    *,
    last_season: LastSeason | None,
    position: str | None,
    baselines: Baselines,
    params: ImputeParams = DEFAULT_PARAMS,
) -> ImputedLine:
    """`per_game` with DD, TD, OREB, DREB and PF filled where it lacks them.

    Never overwrites a stat the source gave. A stat that cannot be imputed (no REB to split, no
    MIN to foul over, none of the five double categories) stays absent.
    """
    out = {name: float(value) for name, value in per_game.items()}
    imputed: dict[str, str] = {}
    history = last_season if last_season is not None and last_season.games > 0 else None
    seasoned = history is not None and history.games >= params.min_gp
    group, group_method = baselines.for_position(position)
    last = history.per_game if history is not None else {}

    # Rebounds: split REB into OREB + DREB.
    if "REB" in out:
        reb = max(out["REB"], 0.0)
        if "OREB" not in out:
            share, method = None, group_method
            if seasoned and last.get("REB", 0.0) > 0 and "OREB" in last:
                share, method = last["OREB"] / last["REB"], METHOD_OWN
            if share is None:
                share = group.oreb_share
            if share is not None:
                out["OREB"] = _clamp(reb * share, 0.0, reb)
                imputed["OREB"] = method
        if "DREB" not in out and "OREB" in out:
            out["DREB"] = max(reb - out["OREB"], 0.0)
            imputed["DREB"] = METHOD_FROM_REB

    # Personal fouls: minutes x a foul rate.
    if "PF" not in out and "MIN" in out:
        rate, method = None, group_method
        if seasoned and last.get("MIN", 0.0) > 0 and "PF" in last:
            rate, method = last["PF"] / last["MIN"], METHOD_OWN
        if rate is None:
            rate = group.pf_per_min
        if rate is not None:
            out["PF"] = max(out["MIN"] * rate, 0.0)
            imputed["PF"] = method

    # Doubles: his own rate blended with the model of the projected line.
    if any(name in out for name in DOUBLE_CATEGORIES) and not ("DD" in out and "TD" in out):
        model_dd, model_td = double_rates(out, params.sd_k)
        # No own rate for a stat (a rookie, or a split without it) -> the model alone.
        weight = blend_weight(history.games if history else None, params.prior_gp)
        if "DD" not in out:
            own = last.get("DD")
            w = weight if own is not None else 0.0
            out["DD"] = _clamp(w * (own or 0.0) + (1 - w) * model_dd, 0.0, 1.0)
            imputed["DD"] = METHOD_BLEND if w > 0 else METHOD_MODEL
        if "TD" not in out:
            own = last.get("TD")
            w = weight if own is not None else 0.0
            out["TD"] = _clamp(w * (own or 0.0) + (1 - w) * model_td, 0.0, out["DD"])
            imputed["TD"] = METHOD_BLEND if w > 0 else METHOD_MODEL

    return ImputedLine(per_game=out, imputed=imputed)


# --- calibration ------------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationSample:
    """One player's actual per-game line, his actual DD rate, and his games (the weight)."""

    per_game: Mapping[str, float]
    dd_rate: float
    games: float


@dataclass(frozen=True)
class Fit:
    k: float
    # GP-weighted RMSE of the model's DD rate against the actual one, in DD per game.
    rmse: float
    # GP-weighted sd of the actual DD rates: the RMSE of predicting the mean for everyone.
    spread: float
    r_squared: float
    players: int


def _weighted_sse(samples: list[CalibrationSample], k: float) -> float:
    return sum(s.games * (double_rates(s.per_game, k)[0] - s.dd_rate) ** 2 for s in samples)


def fit_sd_k(samples: Iterable[CalibrationSample], *, low: float = 0.05, high: float = 5.0) -> Fit:
    """The k minimising the GP-weighted squared error of the modelled DD rate.

    A coarse grid over [low, high] to find the basin, then golden-section search inside it.
    """
    pool = [s for s in samples if s.games > 0]
    if not pool:
        raise ValueError("no calibration samples with games played")

    steps = 100
    grid = [low + (high - low) * i / steps for i in range(steps + 1)]
    errors = [_weighted_sse(pool, k) for k in grid]
    best = min(range(len(grid)), key=errors.__getitem__)
    a, b = grid[max(best - 1, 0)], grid[min(best + 1, steps)]

    ratio = (math.sqrt(5) - 1) / 2
    c, d = b - ratio * (b - a), a + ratio * (b - a)
    fc, fd = _weighted_sse(pool, c), _weighted_sse(pool, d)
    while b - a > 1e-4:
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - ratio * (b - a)
            fc = _weighted_sse(pool, c)
        else:
            a, c, fc = c, d, fd
            d = a + ratio * (b - a)
            fd = _weighted_sse(pool, d)
    k = (a + b) / 2

    weight = sum(s.games for s in pool)
    mean = sum(s.games * s.dd_rate for s in pool) / weight
    sst = sum(s.games * (s.dd_rate - mean) ** 2 for s in pool)
    sse = _weighted_sse(pool, k)
    return Fit(
        k=k,
        rmse=math.sqrt(sse / weight),
        spread=math.sqrt(sst / weight),
        r_squared=1 - sse / sst if sst > 0 else 0.0,
        players=len(pool),
    )
