"""ESPN stat ids and the names we use for them everywhere else.

ESPN identifies stats by integer id in both `mSettings` (which coefficient applies) and in
stat splits (what a player actually did). `espn-api` already ships that mapping, so we reuse
it instead of maintaining a second copy that could drift.
"""

from espn_api.basketball.constant import STATS_MAP as _ESPN_STATS_MAP

# espn-api keys STATS_MAP by string; ESPN's JSON uses ints. Normalise to int keys and drop
# the placeholder entries (blank names / ids that are just their own number).
STAT_ID_TO_NAME: dict[int, str] = {
    int(stat_id): name for stat_id, name in _ESPN_STATS_MAP.items() if name and name != stat_id
}

STAT_NAME_TO_ID: dict[str, int] = {name: stat_id for stat_id, name in STAT_ID_TO_NAME.items()}

# Human-readable labels for the stats a points league can score. Only used for display and
# for the sync summary; scoring itself never depends on these.
STAT_LABELS: dict[str, str] = {
    "PTS": "Points",
    "BLK": "Blocks",
    "STL": "Steals",
    "AST": "Assists",
    "OREB": "Offensive rebounds",
    "DREB": "Defensive rebounds",
    "REB": "Rebounds",
    "EJ": "Ejections",
    "FF": "Flagrant fouls",
    "PF": "Personal fouls",
    "TF": "Technical fouls",
    "TO": "Turnovers",
    "DQ": "Disqualifications",
    "FGM": "Field goals made",
    "FGA": "Field goals attempted",
    "FTM": "Free throws made",
    "FTA": "Free throws attempted",
    "3PM": "Three pointers made",
    "3PA": "Three pointers attempted",
    "FGMI": "Field goals missed",
    "FTMI": "Free throws missed",
    "3PMI": "Three pointers missed",
    "DD": "Double-doubles",
    "TD": "Triple-doubles",
    "QD": "Quadruple-doubles",
    "MIN": "Minutes",
    "GS": "Games started",
    "GP": "Games played",
}


def stat_name(stat_id: int) -> str:
    """Name for an ESPN stat id, falling back to `STAT_<id>` for ids we don't know.

    An unknown id means ESPN added a stat; scoring must still work, so we keep the rule with
    a synthetic name rather than dropping points on the floor.
    """
    return STAT_ID_TO_NAME.get(stat_id, f"STAT_{stat_id}")


def stat_label(name: str) -> str:
    """Display label for a stat name; the name itself when we have nothing friendlier."""
    return STAT_LABELS.get(name, name)
