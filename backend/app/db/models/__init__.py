"""ORM models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogenerate diffs against — so each new model module must be imported here.
"""

from app.db.models.adp import AdpEntry
from app.db.models.league_settings import LeagueSettings, ScoringRule
from app.db.models.player import Player, PlayerAlias
from app.db.models.projection import Projection

__all__ = [
    "AdpEntry",
    "LeagueSettings",
    "Player",
    "PlayerAlias",
    "Projection",
    "ScoringRule",
]
