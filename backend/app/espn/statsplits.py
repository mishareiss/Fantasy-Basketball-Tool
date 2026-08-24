"""Pull ESPN's projected full-season split out of a `kona_player_info` entry.

The payload we already fetch for player identity carries a `stats` array per player, and every
element is one *split* identified by two ids:

* `statSourceId` — 0 for what actually happened, 1 for ESPN's projection.
* `statSplitTypeId` — 0 for the full season; 1/2/3 are last-7/15/30-day windows.

The projection we want is therefore `(statSourceId=1, statSplitTypeId=0)`. Each split holds
season totals in `stats` and per-game averages in `averageStats`, both keyed by ESPN stat id,
plus `appliedTotal`/`appliedAverage` — ESPN's own fantasy points for that split *under our
league's custom scoring*, which makes them a free check on our scoring engine.

Nothing here scores anything; see `app.scoring.projections` for that.
"""

from dataclasses import dataclass, field
from typing import Any

from app.espn.players import player_object
from app.scoring.stats import STAT_ID_TO_NAME

PROJECTED_SOURCE_ID = 1
ACTUAL_SOURCE_ID = 0
FULL_SEASON_SPLIT_ID = 0

GAMES_PLAYED = "GP"

# ESPN stat ids that are genuine counts — the only ones a points league can multiply by a
# coefficient. A split also carries derived stats under the same ids (19-22 shooting
# percentages, 26-36 per-game and ratio figures, 44 FT rate); none are scored in our league
# today, but a settings change is all it would take, and scoring a *percentage* would be
# silently wrong. Dropping them at parse time makes that impossible.
COUNTING_STAT_IDS = frozenset(
    {
        0,  # PTS
        1,  # BLK
        2,  # STL
        3,  # AST
        4,  # OREB
        5,  # DREB
        6,  # REB
        7,  # EJ
        8,  # FF
        9,  # PF
        10,  # TF
        11,  # TO
        12,  # DQ
        13,  # FGM
        14,  # FGA
        15,  # FTM
        16,  # FTA
        17,  # 3PM
        18,  # 3PA
        23,  # FGMI
        24,  # FTMI
        25,  # 3PMI
        37,  # DD
        38,  # TD
        39,  # QD
        40,  # MIN
        41,  # GS
        42,  # GP
        43,  # TW
    }
)


@dataclass(frozen=True)
class ProjectionSplit:
    """One player's projected full season, normalised to stat names.

    `stats` are season totals and `average_stats` the same stats per game — both filtered to
    `COUNTING_STAT_IDS`, so every value in either map is safe to multiply by a coefficient.
    """

    espn_player_id: int
    # The season ESPN says this projection is *for*, which is not always the season we asked
    # for — see `select_projected_split`.
    season: int
    stats: dict[str, float] = field(default_factory=dict)
    average_stats: dict[str, float] = field(default_factory=dict)
    projected_games: float | None = None

    # ESPN's own fantasy points for this split under our custom scoring. Stored so a bad
    # scoring load shows up as a mismatch instead of quietly ranking the wrong players.
    espn_applied_total: float | None = None
    espn_applied_average: float | None = None


def _as_float(value: Any) -> float | None:
    """Floats and ints only. ESPN sends null for stats it has no number for, and null != 0."""
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _counting_stats(raw: Any) -> dict[str, float]:
    """`{stat_id: value}` as ESPN sends it -> `{stat_name: float}`, counting stats only."""
    if not isinstance(raw, dict):
        return {}

    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            stat_id = int(key)
        except (TypeError, ValueError):
            continue
        number = _as_float(value)
        if stat_id in COUNTING_STAT_IDS and number is not None:
            out[STAT_ID_TO_NAME.get(stat_id, f"STAT_{stat_id}")] = number
    return out


def select_projected_split(player: dict[str, Any], season: int) -> dict[str, Any] | None:
    """The projected full-season split for `season`, else the most recent one ESPN has.

    ESPN only publishes a season's projections once its preseason is under way; before that
    the newest projected split is still the *previous* season's. Falling back keeps the board
    populated out of season, and the split's own `seasonId` rides along on the row, so a
    stand-in is never mistaken for a projection of the season we asked for.

    Returns None when ESPN has no projected split at all — the normal case for rookies,
    two-way players, and anyone far enough down the pool that ESPN hasn't published one.
    """
    candidates = [
        split
        for split in player.get("stats") or []
        if isinstance(split, dict)
        and split.get("statSourceId") == PROJECTED_SOURCE_ID
        and split.get("statSplitTypeId") == FULL_SEASON_SPLIT_ID
        and split.get("stats")
    ]
    if not candidates:
        return None

    for split in candidates:
        if split.get("seasonId") == season:
            return split
    return max(candidates, key=lambda split: split.get("seasonId") or 0)


def parse_projection_entry(entry: dict[str, Any], season: int) -> ProjectionSplit | None:
    """Parse the projected split out of one `players[]` entry, or None if there isn't one."""
    player = player_object(entry)
    if player is None:
        return None

    player_id = player.get("id", entry.get("id"))
    if player_id is None:
        return None

    split = select_projected_split(player, season)
    if split is None:
        return None

    stats = _counting_stats(split.get("stats"))
    if not stats:
        return None

    games = stats.get(GAMES_PLAYED)

    return ProjectionSplit(
        espn_player_id=int(player_id),
        season=int(split.get("seasonId") or season),
        stats=stats,
        average_stats=_counting_stats(split.get("averageStats")),
        # ESPN reports 0 games for players it projects but expects not to play; that is not a
        # usable per-game divisor, so it reads as "unknown".
        projected_games=games if games and games > 0 else None,
        espn_applied_total=_as_float(split.get("appliedTotal")),
        espn_applied_average=_as_float(split.get("appliedAverage")),
    )


def parse_projections(entries: list[dict[str, Any]], season: int) -> list[ProjectionSplit]:
    """Parse a whole pool, de-duplicating by ESPN id the way `parse_player_pool` does."""
    splits: dict[int, ProjectionSplit] = {}
    for entry in entries:
        split = parse_projection_entry(entry, season)
        if split is not None:
            splits[split.espn_player_id] = split
    return list(splits.values())
