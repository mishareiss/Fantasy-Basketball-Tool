"""Shared fixtures.

The suite is deliberately offline: parsing and scoring run against recorded ESPN responses in
`tests/fixtures/`, and database behaviour runs against in-memory SQLite. Nothing here needs
cookies or a running Postgres, so `make test` works on a cold checkout.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Player  # noqa: F401  # registers every table on Base.metadata

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text())


@pytest.fixture(scope="session")
def msettings_payload() -> dict[str, Any]:
    """Our real league's `mSettings` response, sanitized (see scripts/record_fixtures.py)."""
    return load_fixture("espn_msettings.json")


@pytest.fixture(scope="session")
def player_pool_payload() -> list[dict[str, Any]]:
    """A sanitized slice of our real `kona_player_info` response."""
    return load_fixture("espn_player_pool.json")


@pytest.fixture
def db() -> Session:
    """A throwaway SQLite database with the real schema, one per test."""
    # StaticPool + check_same_thread keeps the one in-memory database alive across the
    # TestClient's worker thread; the default pool would hand that thread an empty one.
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
