"""Turn a projected stat line into fantasy points under our custom formula.

ESPN scores *completed* games for us, so this is the one place the formula gets applied by
hand — and it is the number the whole draft board sorts on.

Deliberately source-agnostic: it takes plain stat dicts, not an ESPN split, so the imported-CSV
and sportsbook-props sources land here unchanged.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.scoring.engine import ScoringEngine

# How `fantasy_points_per_game` was arrived at. Worth keeping: a projection divided by a
# projected games count says something different from one built out of per-game stats.
PerGameBasis = Literal["projected_games", "per_game_stats", "unavailable"]


@dataclass(frozen=True)
class ScoredProjection:
    """A projection priced under our scoring."""

    fantasy_points_total: float
    fantasy_points_per_game: float
    per_game_basis: PerGameBasis


def score_projection(
    engine: ScoringEngine,
    season_totals: Mapping[str, float],
    *,
    per_game_stats: Mapping[str, float] | None = None,
    projected_games: float | None = None,
) -> ScoredProjection:
    """Score season totals, then derive a per-game number.

    Per-game preference order, and why:

    1. **total / projected games.** Preferred, because it keeps season and per-game value
       exactly consistent — a board that ranks on per-game and a plan that budgets season
       totals can never disagree about the same player.
    2. **Score the per-game stats directly.** Used when the source gives no games count.
       Arithmetically the same number when the source is internally consistent (it is for
       ESPN, whose `averageStats` are just `stats / GP`), so this is a fallback, not a
       different opinion.
    3. **0.0**, flagged `unavailable`, when there is neither. Never guess a games count:
       a made-up 82 would quietly rank a player who may not play at all.
    """
    total = engine.score(season_totals)

    if projected_games and projected_games > 0:
        return ScoredProjection(total, total / projected_games, "projected_games")
    if per_game_stats:
        return ScoredProjection(total, engine.score(per_game_stats), "per_game_stats")
    return ScoredProjection(total, 0.0, "unavailable")
