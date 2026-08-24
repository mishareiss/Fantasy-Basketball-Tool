"""Shared fixtures.

The suite is deliberately offline: parsing and scoring run against recorded ESPN responses in
`tests/fixtures/`, and database behaviour runs against in-memory SQLite. Nothing here needs
cookies or a running Postgres, so `make test` works on a cold checkout.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.ages import NbaPlayer, birthdate_from_payload
from app.config import get_settings
from app.db.base import Base
from app.db.models import Player  # noqa: F401  # registers every table on Base.metadata

# Ages are computed at a fixed date, never `today`, so the expected numbers below never rot.
AGE_AS_OF = date(2026, 10, 1)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def pinned_age_as_of():
    """Pin AGE_AS_OF for the whole suite, so nobody's local .env can move the expected ages."""
    settings = get_settings()
    original = settings.age_as_of
    settings.age_as_of = AGE_AS_OF
    yield AGE_AS_OF
    settings.age_as_of = original


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


@pytest.fixture(scope="session")
def nba_static_payload() -> list[dict[str, Any]]:
    """nba.com's offline roster, narrowed to the ESPN fixture pool (scripts/record_nba_fixtures)."""
    return load_fixture("nba_static_players.json")


@pytest.fixture(scope="session")
def nba_info_payload() -> dict[str, Any]:
    """Recorded `CommonPlayerInfo` responses, keyed by nba id — the birthdate half only."""
    return load_fixture("nba_common_player_info.json")


@pytest.fixture(scope="session")
def nba_players(nba_static_payload) -> list[NbaPlayer]:
    """The recorded roster as the age sync sees it."""
    return [
        NbaPlayer(
            nba_id=int(entry["id"]),
            full_name=entry["full_name"],
            first_name=entry.get("first_name"),
            last_name=entry.get("last_name"),
            is_active=bool(entry.get("is_active")),
        )
        for entry in nba_static_payload
    ]


@pytest.fixture
def fetch_recorded_birthdate(nba_info_payload):
    """A `BirthdateFetcher` backed by the recorded responses, so no test touches nba.com.

    Raises on an id we never recorded rather than returning None, because a test asking for an
    unrecorded player is a broken test, not a player without a birthday.
    """

    def fetch(nba_id: int) -> date | None:
        payload = nba_info_payload.get(str(nba_id))
        if payload is None:
            raise AssertionError(f"no recorded CommonPlayerInfo for nba id {nba_id}")
        return birthdate_from_payload(payload)

    return fetch


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
