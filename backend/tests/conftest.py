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
from app.espn.players import parse_player_pool
from app.espn.sync import SyncSummary, sync_players

# Ages are computed at a fixed date, never `today`, so the expected numbers below never rot.
AGE_AS_OF = date(2026, 10, 1)

# The season the fixtures are for. ESPN labels a season by the year it ends.
SEASON = 2027

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def pinned_settings():
    """Pin AGE_AS_OF and ESPN_SEASON for the whole suite.

    Nobody's local `.env` should be able to move the expected ages, and the import pipeline
    falls back to `ESPN_SEASON` when a caller omits the season — so a checkout with no league
    configured would otherwise fail a test about seasons rather than one about configuration.
    """
    settings = get_settings()
    original = (settings.age_as_of, settings.espn_season)
    settings.age_as_of, settings.espn_season = AGE_AS_OF, SEASON
    yield AGE_AS_OF
    settings.age_as_of, settings.espn_season = original


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
def adp_csv() -> str:
    """A synthetic ADP export: exact, accented, inverted, fuzzy, unmatched and unusable rows.

    Hand-written rather than recorded, because the point of it is the awkward cases — a typo
    only a fuzzy match catches, a name we don't carry, a row with no ADP at all — and a real
    export happens to contain whichever of those it contains.
    """
    return (FIXTURE_DIR / "adp_sample.csv").read_text(encoding="utf-8-sig")


@pytest.fixture
def players(db, player_pool_payload) -> Session:
    """A database holding only our canonical players — what an import resolves names against."""
    sync_players(
        db, parse_player_pool(player_pool_payload), SyncSummary(league_id=0, season=SEASON)
    )
    db.commit()
    return db


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
