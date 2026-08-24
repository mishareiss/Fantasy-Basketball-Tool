"""The age endpoints: the sync, the worklist it produces, and the hand-resolve path out of it."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.ages import NBA_SOURCE, sync_ages
from app.db.models import Player, PlayerAlias
from app.db.session import get_db
from app.espn.players import parse_player_pool
from app.espn.sync import SyncSummary, sync_players
from app.main import app
from tests.conftest import AGE_AS_OF

PROCIDA = 4871139  # in ESPN's pool, unknown to nba.com's bundled roster
LEBRON = 1966


@pytest.fixture
def api(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def aged(db, player_pool_payload, nba_players, fetch_recorded_birthdate):
    """Players synced from ESPN, then aged from the recorded nba.com responses."""
    sync_players(db, parse_player_pool(player_pool_payload), SyncSummary(league_id=1, season=2027))
    db.commit()
    return sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )


def test_the_worklist_holds_the_players_with_no_age(api, aged):
    body = api.get("/players/unresolved?need=age").json()

    assert body["source"] == NBA_SOURCE
    assert body["total"] == aged.players_missing_age
    names = [row["name"] for row in body["players"]]
    assert "Gabriele Procida" in names
    assert "LeBron James" not in names
    # Nobody on this list has an nba alias yet — that is exactly why they're on it.
    assert all(row["has_alias"] is False for row in body["players"])


def test_the_worklist_puts_the_players_who_matter_first(api, db, aged):
    """A missing age on a projected starter costs more than one on a two-way contract."""
    body = api.get("/players/unresolved").json()
    scores = [row["fantasy_points_per_game"] or 0.0 for row in body["players"]]

    assert scores == sorted(scores, reverse=True)


def test_an_unknown_need_is_rejected(api, aged):
    assert api.get("/players/unresolved?need=height").status_code == 400


def test_a_hand_made_alias_unblocks_the_next_sync(
    api, db, aged, nba_players, fetch_recorded_birthdate
):
    """The long-tail escape hatch, end to end: alias -> next sync -> a real age."""
    assert db.get(Player, PROCIDA).age is None

    # Pretend a human looked Procida up on nba.com and found Bam Adebayo's id. (Any recorded
    # id will do; the point is that the alias, not the matcher, decides.)
    response = api.post(
        f"/players/{PROCIDA}/aliases",
        json={"source": NBA_SOURCE, "source_id": "1628389", "source_name": "Gabriele Procida"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["created"] is True
    assert body["match_method"] == "manual"
    assert body["confidence"] == 1.0

    sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    assert db.get(Player, PROCIDA).birthdate is not None
    assert db.get(Player, PROCIDA).age is not None


def test_recording_the_same_alias_twice_updates_it_instead_of_duplicating(api, db, aged):
    payload = {"source": NBA_SOURCE, "source_id": "1628389", "source_name": "Gabriele Procida"}
    api.post(f"/players/{PROCIDA}/aliases", json=payload)
    second = api.post(f"/players/{PROCIDA}/aliases", json=payload)

    assert second.json()["created"] is False
    assert (
        len(
            list(
                db.scalars(select(PlayerAlias).where(PlayerAlias.source_name == "Gabriele Procida"))
            )
        )
        == 1
    )


def test_an_alias_for_a_player_we_do_not_have_is_a_404(api, aged):
    response = api.post(
        "/players/1234567890/aliases", json={"source": NBA_SOURCE, "source_name": "Nobody"}
    )

    assert response.status_code == 404


def test_an_alias_on_a_player_we_already_aged_reports_the_age(api, db, aged):
    """Aliasing an already-aged player is a no-op worth confirming rather than an error."""
    body = api.post(
        f"/players/{LEBRON}/aliases",
        json={"source": "hashtag", "source_name": "LeBron James"},
    ).json()

    assert body["age"] == 41
    assert body["birthdate"] == "1984-12-30"
