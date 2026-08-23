"""Apply our league's custom scoring formula to a stat line.

ESPN already scores *completed* games under our formula, so this is only needed where we
invent the stats ourselves: projections. That's exactly where the draft board lives.
"""

from collections.abc import Iterable, Mapping
from typing import Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import LeagueSettings, ScoringRule
from app.scoring.stats import STAT_ID_TO_NAME


@runtime_checkable
class Coefficient(Protocol):
    """What scoring needs from a rule — satisfied by both `ScoringRule` and `ScoringCoefficient`."""

    stat_id: int
    stat_name: str
    points: float


StatLine = Mapping[str | int, float | int | None]


def _lookup(stat_line: StatLine, coefficient: Coefficient) -> float | None:
    """Find this stat in the line, accepting stat names or stat ids (int or str) as keys."""
    for key in (coefficient.stat_name, coefficient.stat_id, str(coefficient.stat_id)):
        if key in stat_line:
            value = stat_line[key]
            return None if value is None else float(value)
    return None


def score_stat_line(stat_line: StatLine, coefficients: Iterable[Coefficient]) -> float:
    """Fantasy points for `stat_line` under our custom formula.

    `stat_line` is keyed by stat name ('PTS') or ESPN stat id (0 or '0') — ESPN hands us both
    shapes depending on the endpoint. Stats with no coefficient contribute nothing, and
    coefficients with no matching stat are skipped rather than treated as zero-valued, so a
    partial projection never silently reads as a bad one.
    """
    total = 0.0
    for coefficient in coefficients:
        value = _lookup(stat_line, coefficient)
        if value is not None:
            total += value * coefficient.points
    return total


def unscored_keys(stat_line: StatLine, coefficients: Iterable[Coefficient]) -> list[str]:
    """Keys in `stat_line` that no coefficient consumed — useful when eyeballing a projection.

    Some are legitimately unscored in our league (e.g. FGA); a typo'd key looks the same, so
    this is a hint for humans, not a validation error.
    """
    consumed: set[str | int] = set()
    for coefficient in coefficients:
        consumed.update({coefficient.stat_name, coefficient.stat_id, str(coefficient.stat_id)})
    leftover = [key for key in stat_line if key not in consumed]
    return sorted(str(key) for key in leftover)


class ScoringEngine:
    """The stored coefficients, ready to score many stat lines."""

    def __init__(self, coefficients: Iterable[Coefficient]) -> None:
        self.coefficients: tuple[Coefficient, ...] = tuple(coefficients)

    def score(self, stat_line: StatLine) -> float:
        return score_stat_line(stat_line, self.coefficients)

    def as_points_map(self) -> dict[str, float]:
        return {c.stat_name: c.points for c in self.coefficients}

    def __len__(self) -> int:
        return len(self.coefficients)


class ScoringRulesNotLoaded(RuntimeError):
    """No stored scoring rules for this league/season — run a league sync first."""


def load_scoring_engine(db: Session, espn_league_id: int, season: int) -> ScoringEngine:
    """Build a `ScoringEngine` from the rules a previous sync stored."""
    rules = db.scalars(
        select(ScoringRule)
        .join(LeagueSettings)
        .where(
            LeagueSettings.espn_league_id == espn_league_id,
            LeagueSettings.season == season,
        )
        .order_by(ScoringRule.stat_id)
    ).all()
    if not rules:
        raise ScoringRulesNotLoaded(
            f"No scoring rules stored for league {espn_league_id} season {season}; "
            "run POST /sync/league (or `make sync`) first."
        )
    return ScoringEngine(rules)


def normalise_stat_line(stat_line: StatLine) -> dict[str, float]:
    """Rewrite a stat line so every key we recognise becomes a stat name.

    ESPN's raw splits are keyed by stat id; our projection sources will be keyed by name.
    Normalising once keeps everything downstream comparable.
    """
    out: dict[str, float] = {}
    for key, value in stat_line.items():
        if value is None:
            continue
        if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
            name = STAT_ID_TO_NAME.get(int(key), str(key))
        else:
            name = str(key)
        out[name] = float(value)
    return out
