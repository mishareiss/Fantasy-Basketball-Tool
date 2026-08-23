"""ESPN integration: cookie auth, raw v3 views, and the league sync."""

from app.espn.client import (
    ESPNClient,
    ESPNCredentials,
    ESPNCredentialsError,
    ESPNRequestError,
    credentials_available,
    require_credentials,
)
from app.espn.players import PlayerRecord, parse_player_entry, parse_player_pool
from app.espn.sync import SyncSummary, sync_league, sync_players, sync_scoring_settings

__all__ = [
    "ESPNClient",
    "ESPNCredentials",
    "ESPNCredentialsError",
    "ESPNRequestError",
    "PlayerRecord",
    "SyncSummary",
    "credentials_available",
    "parse_player_entry",
    "parse_player_pool",
    "require_credentials",
    "sync_league",
    "sync_players",
    "sync_scoring_settings",
]
