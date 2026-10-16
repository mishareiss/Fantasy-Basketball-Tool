"""Database lookups the waiver plan shares: its rows, and those rows as engine moves."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import PlanMove
from app.team.plan import Move


def load_moves(db: Session, season: int, team_id: int) -> list[PlanMove]:
    """Every move of one team's plan with its candidates, in plan order. Two queries."""
    return list(
        db.scalars(
            select(PlanMove)
            .options(selectinload(PlanMove.candidates))
            .where(PlanMove.season == season, PlanMove.team_id == team_id)
            .order_by(PlanMove.scoring_period_id, PlanMove.sequence, PlanMove.id)
        )
    )


def engine_move(row: PlanMove) -> Move:
    """A stored move as the pure engine's `Move`."""
    return Move(
        id=row.id,
        sp=row.scoring_period_id,
        candidates=tuple(c.player_id for c in sorted(row.candidates, key=lambda c: c.rank)),
        sequence=row.sequence,
        drop_player_id=row.drop_player_id,
        drop_from_move_id=row.drop_from_move_id,
        status=row.status,
        resolved_player_id=row.resolved_player_id,
        drop_mismatch=row.drop_mismatch,
        detected_drop_player_id=row.detected_drop_player_id,
        timing=row.timing,
    )
