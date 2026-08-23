"""Liveness and database-connectivity checks."""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_db

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health() -> dict[str, str]:
    """The service is up. Says nothing about its dependencies."""
    return {"status": "ok"}


@router.get("/db")
def health_db(db: Session = Depends(get_db)) -> dict[str, str]:
    """Round-trip a trivial query so a broken DB config surfaces here, not mid-feature."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return {"status": "error", "database": "unreachable", "detail": str(exc.__class__.__name__)}
    return {"status": "ok", "database": "connected"}
