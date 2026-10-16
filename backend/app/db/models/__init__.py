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
from app.db.models.plan import (
    PLAN_DONE,
    PLAN_PLANNED,
    PLAN_SKIPPED,
    PLAN_STATUSES,
    PLAN_TIMINGS,
    TIMING_AFTER_TIPOFF,
    TIMING_BEFORE_GAMES,
    AcquisitionOverride,
    PlanCandidate,
    PlanLineupAssignment,
    PlanMove,
)
from app.db.models.player import Player, PlayerAlias
from app.db.models.projection import Projection
from app.db.models.ranking import RankingEntry, RankingSet
from app.db.models.team import (
    LINEUP_SOURCE_ESPN,
    LINEUP_SOURCE_MANUAL,
    LINEUP_SOURCES,
    EspnTransaction,
    FantasyTeam,
    LineupAssignment,
    NbaGame,
    RosterEntry,
)
from app.db.models.watchlist import WatchlistEntry

__all__ = [
    "DRAFT_MODES",
    "LINEUP_SOURCES",
    "LINEUP_SOURCE_ESPN",
    "LINEUP_SOURCE_MANUAL",
    "MASTER_TAGS",
    "MODE_MANUAL",
    "MODE_SIMULATION",
    "PLAN_DONE",
    "PLAN_PLANNED",
    "PLAN_SKIPPED",
    "PLAN_STATUSES",
    "PLAN_TIMINGS",
    "SCOPE_OVERALL",
    "TIER_SCOPES",
    "TIMING_AFTER_TIPOFF",
    "TIMING_BEFORE_GAMES",
    "AcquisitionOverride",
    "AdpEntry",
    "Draft",
    "DraftPick",
    "EspnTransaction",
    "FantasyTeam",
    "LeagueSettings",
    "LineupAssignment",
    "MarketLine",
    "MasterRankEntry",
    "MasterTierBreak",
    "NbaGame",
    "PlanCandidate",
    "PlanLineupAssignment",
    "PlanMove",
    "Player",
    "PlayerAlias",
    "Projection",
    "RankingEntry",
    "RankingSet",
    "RosterEntry",
    "ScoringRule",
    "WatchlistEntry",
]
