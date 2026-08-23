"""ORM models. Real domain tables (Player, RankingSet, DraftPick, ...) land here next.

Importing this package registers every model on `Base.metadata`, which is what Alembic
autogenerate diffs against — so each new model module must be imported here.
"""

from app.db.models.health_check import HealthCheck

__all__ = ["HealthCheck"]
