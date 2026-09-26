"""ORM models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogenerate diffs against — so each new model module must be imported here.
"""

from app.db.models.adp import AdpEntry
from app.db.models.draft import DRAFT_MODES, MODE_MANUAL, MODE_SIMULATION, Draft, DraftPick
from app.db.models.league_settings import LeagueSettings, ScoringRule
from app.db.models.market_line import MarketLine
from app.db.models.master_rank import MASTER_TAGS, MasterRankEntry
from app.db.models.master_tier import SCOPE_OVERALL, TIER_SCOPES, MasterTierBreak
from app.db.models.player import Player, PlayerAlias
from app.db.models.projection import Projection
from app.db.models.ranking import RankingEntry, RankingSet

__all__ = [
    "DRAFT_MODES",
    "MASTER_TAGS",
    "MODE_MANUAL",
    "MODE_SIMULATION",
    "SCOPE_OVERALL",
    "TIER_SCOPES",
    "AdpEntry",
    "Draft",
    "DraftPick",
    "LeagueSettings",
    "MarketLine",
    "MasterRankEntry",
    "MasterTierBreak",
    "Player",
    "PlayerAlias",
    "Projection",
    "RankingEntry",
    "RankingSet",
    "ScoringRule",
]
