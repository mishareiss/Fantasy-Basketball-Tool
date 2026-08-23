"""ORM models.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogenerate diffs against — so each new model module must be imported here.
"""

from app.db.models.league_settings import LeagueSettings, ScoringRule
from app.db.models.player import Player, PlayerAlias

__all__ = ["LeagueSettings", "Player", "PlayerAlias", "ScoringRule"]
