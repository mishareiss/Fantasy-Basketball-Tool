"""Parse ESPN's `mSettings` payload into the league configuration we store."""

from dataclasses import dataclass, field
from typing import Any

from espn_api.basketball.constant import POSITION_MAP

from app.scoring.stats import stat_name


@dataclass(frozen=True)
class ScoringCoefficient:
    """`points` fantasy points per unit of the stat — one entry of our custom formula."""

    stat_id: int
    stat_name: str
    points: float
    is_reverse: bool = False


@dataclass(frozen=True)
class LeagueScoringSettings:
    """Everything we keep from `mSettings`."""

    name: str | None = None
    scoring_type: str | None = None
    team_count: int | None = None
    roster_slots: dict[str, int] = field(default_factory=dict)
    coefficients: tuple[ScoringCoefficient, ...] = ()

    def as_points_map(self) -> dict[str, float]:
        """`{stat_name: points}` — the shape that's easiest to eyeball."""
        return {c.stat_name: c.points for c in self.coefficients}


class ESPNSettingsError(ValueError):
    """The mSettings payload wasn't shaped the way we expect."""


def _slot_name(slot_id: str | int) -> str:
    """ESPN lineup slot id -> its abbreviation ('PG', 'UT', 'BE', 'IR')."""
    name = POSITION_MAP.get(int(slot_id), "")
    return name or f"SLOT_{slot_id}"


def parse_scoring_items(items: list[dict[str, Any]]) -> tuple[ScoringCoefficient, ...]:
    """Turn `scoringSettings.scoringItems` into coefficients, ordered by stat id.

    ESPN returns these in an arbitrary order and only includes stats the league actually
    scores, so a missing stat legitimately means zero points.
    """
    coefficients = [
        ScoringCoefficient(
            stat_id=int(item["statId"]),
            stat_name=stat_name(int(item["statId"])),
            points=float(item.get("points", 0.0)),
            is_reverse=bool(item.get("isReverseItem", False)),
        )
        for item in items
    ]
    return tuple(sorted(coefficients, key=lambda c: c.stat_id))


def parse_league_settings(payload: dict[str, Any]) -> LeagueScoringSettings:
    """Parse a raw `?view=mSettings` response body.

    Accepts either the full response or just its `settings` object, since ESPN's older
    league-history endpoint wraps the same shape differently.
    """
    settings = payload.get("settings", payload)
    if not isinstance(settings, dict) or "scoringSettings" not in settings:
        raise ESPNSettingsError("mSettings payload has no `scoringSettings` block")

    scoring = settings["scoringSettings"]
    items = scoring.get("scoringItems")
    if not items:
        raise ESPNSettingsError("mSettings payload has no `scoringItems` — cannot score anything")

    roster = settings.get("rosterSettings", {}) or {}
    slot_counts = roster.get("lineupSlotCounts", {}) or {}

    return LeagueScoringSettings(
        name=settings.get("name"),
        scoring_type=scoring.get("scoringType"),
        team_count=settings.get("size"),
        # Slots ESPN reports as zero aren't part of our roster; drop them for readability.
        roster_slots={_slot_name(k): int(v) for k, v in slot_counts.items() if int(v) > 0},
        coefficients=parse_scoring_items(items),
    )
