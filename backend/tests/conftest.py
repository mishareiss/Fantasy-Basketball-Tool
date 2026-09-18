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

from app.ages import NbaPlayer, birthdate_from_payload, sync_ages
from app.config import get_settings
from app.db.base import Base
from app.db.models import (
    Player,  # noqa: F401  # registers every table on Base.metadata
    RankingEntry,
    RankingSet,
)
from app.espn.ownership import parse_ownership
from app.espn.players import parse_player_pool
from app.espn.statsplits import parse_projections
from app.espn.sync import (
    SyncSummary,
    sync_adp,
    sync_players,
    sync_projections,
    sync_scoring_settings,
)
from app.ingest.market_line import MARKET_SOURCE, upsert_market_line
from app.ingest.parser import ParsedRow
from app.ingest.registry import ResolvedRow, UpsertContext, UpsertCounts
from app.scoring import ScoringEngine, parse_league_settings

# Ages are computed at a fixed date, never `today`, so the expected numbers below never rot.
AGE_AS_OF = date(2026, 10, 1)

# The season the fixtures are for. ESPN labels a season by the year it ends.
SEASON = 2027

# Stand-in for our real league id, which the fixture recorder strips.
LEAGUE_ID = 999999

# The dynasty curve the suite expects, pinned for the same reason ages are: these are env
# vars, and a curve calibrated in someone's `.env` must not be able to re-rank the board a
# test is asserting about. Keep in step with the `Settings` defaults.
DYNASTY_CURVE = {
    "dynasty_prime_start": 24,
    "dynasty_prime_end": 27,
    "dynasty_youth_bonus_per_year": 0.04,
    "dynasty_decline_per_year": 0.05,
    "dynasty_min_multiplier": 0.40,
}

# The tiering parameters the suite expects, pinned for the same reason the curve is: these
# are env vars, and someone's locally-calibrated TIER_* would otherwise re-cut a board a test
# is asserting the tiers of. Keep in step with the `Settings` defaults.
TIER_PARAMS = {
    "tier_gap_multiple": 2.0,
    "tier_min_size": 2,
    "tier_max": 15,
    "tier_pool": 150,
}

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def pinned_settings():
    """Pin AGE_AS_OF, ESPN_SEASON, ESPN_LEAGUE_ID, the DYNASTY_* curve and the TIER_* params.

    Nobody's local `.env` should be able to move the expected ages, and the import pipeline
    falls back to `ESPN_SEASON` when a caller omits the season — so a checkout with no league
    configured would otherwise fail a test about seasons rather than one about configuration.
    The league id is pinned for the same reason: a projection import looks up the scoring
    coefficients for the configured league, and the fixtures are stored under `LEAGUE_ID`.
    And the dynasty curve, for the same reason again: it is what orders the dynasty board —
    as the tier parameters are what cuts it up.
    """
    settings = get_settings()
    pinned = DYNASTY_CURVE | TIER_PARAMS
    original = (settings.age_as_of, settings.espn_season, settings.espn_league_id)
    original_pinned = {field: getattr(settings, field) for field in pinned}
    settings.age_as_of = AGE_AS_OF
    settings.espn_season = SEASON
    settings.espn_league_id = LEAGUE_ID
    for field, value in pinned.items():
        setattr(settings, field, value)
    yield AGE_AS_OF
    settings.age_as_of, settings.espn_season, settings.espn_league_id = original
    for field, value in original_pinned.items():
        setattr(settings, field, value)


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


@pytest.fixture(scope="session")
def projection_csv() -> str:
    """A synthetic per-game projection export, deliberately awkward in every direction.

    Header variants a real source would use (TREB, 3PTM, TOV, MPG), derived columns nothing
    can score (FG%, FT%, Rank), and the same spread of names the ADP fixture carries — exact,
    accented, inverted, a typo only a fuzzy match catches, two we carry nobody for, a row with
    no points at all, one with no games count, and a duplicate.
    """
    return (FIXTURE_DIR / "projection_sample.csv").read_text(encoding="utf-8-sig")


@pytest.fixture(scope="session")
def ranking_csv() -> str:
    """A synthetic ranking export WITH a rank column: gaps in the source's own numbering,
    numeric and named tiers, an empty score, and the same awkward cast of names.

    The gaps matter. A source that prints 1, 2, 3, 4, 5, 8, 12 means those numbers, and a
    ranking that quietly renumbered them 1..7 would disagree with the board it came from.
    """
    return (FIXTURE_DIR / "ranking_sample.csv").read_text(encoding="utf-8-sig")


@pytest.fixture(scope="session")
def market_line_csv() -> str:
    """A synthetic sportsbook-props export: LONG format, and awkward in every direction.

    One row per (player, stat), so Jokic appears five times legitimately — the file that
    proves the pipeline's duplicate rule is about (player, stat) here and not about the
    player. Priced and unpriced lines, a juiced two-sided line, a combination prop nothing can
    score, a genuine repeat of (Jokic, Points), a row with no line at all, a player we carry
    nobody for, and a typo'd name only a fuzzy match would catch — which this kind refuses,
    because a mis-attributed line is a number you'd bet on.
    """
    return (FIXTURE_DIR / "market_line_sample.csv").read_text(encoding="utf-8-sig")


@pytest.fixture(scope="session")
def ranking_order_csv() -> str:
    """A ranking with NO rank column — the order of the rows *is* the ranking.

    Two of the nine names are players we carry nobody for, sitting at positions 4 and 7 on
    purpose: their ranks have to stay empty rather than pull everyone below them up a place.
    """
    return (FIXTURE_DIR / "ranking_order_sample.csv").read_text(encoding="utf-8-sig")


@pytest.fixture
def players(db, player_pool_payload) -> Session:
    """A database holding only our canonical players — what an import resolves names against."""
    sync_players(
        db, parse_player_pool(player_pool_payload), SyncSummary(league_id=0, season=SEASON)
    )
    db.commit()
    return db


@pytest.fixture
def priced(players, msettings_payload) -> Session:
    """The players, plus our league's stored scoring coefficients.

    What a projection import needs and an ADP import doesn't: an imported stat line is priced
    here, by us, with the same engine that prices ESPN's.
    """
    sync_scoring_settings(
        players,
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        parsed=parse_league_settings(msettings_payload),
        summary=SyncSummary(league_id=LEAGUE_ID, season=SEASON),
    )
    players.commit()
    return players


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


@pytest.fixture
def synced(db, msettings_payload, player_pool_payload) -> SyncSummary:
    """A database in the state a real sync leaves it in: scoring, players, projections, ADP.

    Lives here rather than beside the board tests because the board is no longer the only
    endpoint built on it — `/valuation/tiers` reads the same ranking, and two copies of this
    setup would be two boards to keep in step.
    """
    summary = SyncSummary(league_id=LEAGUE_ID, season=SEASON)
    settings_row = sync_scoring_settings(
        db,
        espn_league_id=LEAGUE_ID,
        season=SEASON,
        parsed=parse_league_settings(msettings_payload),
        summary=summary,
    )
    sync_players(db, parse_player_pool(player_pool_payload), summary)
    sync_projections(
        db,
        parse_projections(player_pool_payload, SEASON),
        ScoringEngine(settings_row.scoring_rules),
        summary,
    )
    sync_adp(db, parse_ownership(player_pool_payload), summary, season=SEASON)
    db.commit()
    return summary


@pytest.fixture
def aged(db, synced, nba_players, fetch_recorded_birthdate) -> Session:
    """The synced board, with nba.com ages filled in — what the dynasty horizon needs."""
    sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )
    return db


@pytest.fixture
def make_market_lines(db):
    """Store sportsbook lines and derive their market projection, the way an import does.

    Goes through the real handler rather than a CSV, for the same reason `make_ranking_set`
    does: what these tests need is control over the NUMBERS — a player priced on two stats, a
    juiced line, a young player whose age the curve has an opinion about — and getting there
    through a paste would be testing the parser again with extra steps. The import path itself
    has its own suite in `test_ingest_market_line`.

    Takes `{player_id: {"PTS": 27.5, "AST": (9.5, -150, 120)}}` — a bare number for an
    unpriced line, a triple for one with American odds on each side.
    """

    def make(
        lines: dict[int, dict[str, float | tuple]],
        *,
        source: str = MARKET_SOURCE,
        season: int = SEASON,
    ) -> UpsertCounts:
        rows = []
        for player_id, stats in lines.items():
            for stat, value in stats.items():
                priced = value if isinstance(value, tuple) else (value, None, None)
                line, over_odds, under_odds = priced
                rows.append(
                    ResolvedRow(
                        player_id=player_id,
                        row=ParsedRow(
                            line=len(rows) + 1,
                            name=str(player_id),
                            values={
                                "stat": stat,
                                "line": float(line),
                                "over_odds": over_odds,
                                "under_odds": under_odds,
                            },
                            index=len(rows) + 1,
                        ),
                    )
                )
        counts = upsert_market_line(
            db, rows, UpsertContext(source=source, season=season, dry_run=False)
        )
        db.commit()
        return counts

    return make


@pytest.fixture
def make_ranking_set(db):
    """Write a rank-only list straight into the tables, at exactly the ranks given.

    The importer has its own tests (`test_ingest_ranking`, `test_api_rankings`); what the
    consensus tests need is control over the *numbers* — a list that stops at four names, one
    that numbers 1, 2, 5, 9 with gaps, one tagged redraft — and going through a CSV to get
    there would be testing the parser again with extra steps.
    """

    def make(
        name: str,
        horizon: str,
        ranks: dict[int, int],
        *,
        source: str = "dizzle",
        season: int = SEASON,
    ) -> RankingSet:
        ranking_set = RankingSet(source=source, name=name, season=season, horizon=horizon)
        ranking_set.entries = [
            RankingEntry(player_id=player_id, rank=rank) for player_id, rank in ranks.items()
        ]
        db.add(ranking_set)
        db.commit()
        return ranking_set

    return make
