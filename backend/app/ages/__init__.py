"""Player ages, from nba.com — the input the dynasty age curve has been waiting on.

ESPN publishes no birthdate anywhere in its fantasy API, so age (the single biggest lever on
dynasty value after production itself) has to come from somewhere else. nba.com's
`CommonPlayerInfo` is authoritative and free; `app.matching` is what connects its names to our
ESPN-keyed players.
"""

from app.ages.nba_source import (
    BIRTHDATE_COLUMN,
    DEFAULT_DELAY,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    NBA_SOURCE,
    NbaApiError,
    NbaPlayer,
    birthdate_from_payload,
    fetch_birthdate,
    fetch_common_player_info,
    nba_api_available,
    parse_birthdate,
    static_players,
)
from app.ages.sync import (
    AgeSyncSummary,
    compute_age,
    fetch_birthdates,
    match_nba_roster,
    players_needing_birthdate,
    recompute_ages,
    record_nba_aliases,
    sync_ages,
)

__all__ = [
    "BIRTHDATE_COLUMN",
    "DEFAULT_DELAY",
    "DEFAULT_RETRIES",
    "DEFAULT_TIMEOUT",
    "NBA_SOURCE",
    "AgeSyncSummary",
    "NbaApiError",
    "NbaPlayer",
    "birthdate_from_payload",
    "compute_age",
    "fetch_birthdate",
    "fetch_birthdates",
    "fetch_common_player_info",
    "match_nba_roster",
    "nba_api_available",
    "parse_birthdate",
    "players_needing_birthdate",
    "recompute_ages",
    "record_nba_aliases",
    "static_players",
    "sync_ages",
]
