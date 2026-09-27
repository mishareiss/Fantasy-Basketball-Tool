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
    parse_actual_entry,
    parse_actuals,
    parse_projection_entry,
    parse_projections,
    select_actual_split,
    select_projected_split,
)
from app.espn.sync import (
    ACTUAL_SEASON_KIND,
    ESPN_SOURCE,
    SEASON_PROJECTION_KIND,
    SyncSummary,
    sync_actuals,
    sync_adp,
    sync_league,
    sync_players,
    sync_projections,
    sync_scoring_settings,
)

__all__ = [
    "ACTUAL_SEASON_KIND",
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
    "parse_actual_entry",
    "parse_actuals",
    "parse_ownership",
    "parse_ownership_entry",
    "parse_player_entry",
    "parse_player_pool",
    "parse_projection_entry",
    "parse_projections",
    "player_object",
    "require_credentials",
    "select_actual_split",
    "select_projected_split",
    "sync_actuals",
    "sync_adp",
    "sync_league",
    "sync_players",
    "sync_projections",
    "sync_scoring_settings",
]
