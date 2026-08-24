"""ESPN integration: cookie auth, raw v3 views, and the league sync."""

from app.espn.client import (
    ESPNClient,
    ESPNCredentials,
    ESPNCredentialsError,
    ESPNRequestError,
    credentials_available,
    require_credentials,
)
from app.espn.ownership import OwnershipRecord, parse_ownership, parse_ownership_entry
from app.espn.players import PlayerRecord, parse_player_entry, parse_player_pool, player_object
from app.espn.statsplits import (
    ProjectionSplit,
    parse_projection_entry,
    parse_projections,
    select_projected_split,
)
from app.espn.sync import (
    ESPN_SOURCE,
    SEASON_PROJECTION_KIND,
    SyncSummary,
    sync_adp,
    sync_league,
    sync_players,
    sync_projections,
    sync_scoring_settings,
)

__all__ = [
    "ESPN_SOURCE",
    "SEASON_PROJECTION_KIND",
    "ESPNClient",
    "ESPNCredentials",
    "ESPNCredentialsError",
    "ESPNRequestError",
    "OwnershipRecord",
    "PlayerRecord",
    "ProjectionSplit",
    "SyncSummary",
    "credentials_available",
    "parse_ownership",
    "parse_ownership_entry",
    "parse_player_entry",
    "parse_player_pool",
    "parse_projection_entry",
    "parse_projections",
    "player_object",
    "require_credentials",
    "select_projected_split",
    "sync_adp",
    "sync_league",
    "sync_players",
    "sync_projections",
    "sync_scoring_settings",
]
