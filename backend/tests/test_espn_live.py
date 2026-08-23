"""Live ESPN checks. Skipped without cookies, so CI and cold checkouts stay green.

Run them deliberately with `uv run pytest -m live`. They're the canary for ESPN changing an
endpoint or our cookies expiring — the two failure modes the recorded fixtures can't see.
"""

import pytest

from app.espn.client import ESPNClient, credentials_available
from app.scoring import parse_league_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not credentials_available(), reason="ESPN cookies not set in .env; skipping live pull"
    ),
]


@pytest.fixture(scope="module")
def client() -> ESPNClient:
    return ESPNClient.from_settings()


def test_msettings_still_exposes_scoring_items(client):
    parsed = parse_league_settings(client.fetch_settings_view())

    assert parsed.scoring_type == "H2H_POINTS"
    assert parsed.coefficients, "ESPN returned no scoring items"
    assert parsed.roster_slots


def test_player_pool_is_a_plausible_size(client):
    entries = client.fetch_player_pool_pages()

    assert len(entries) > 400, "ESPN returned suspiciously few players"


def test_espn_api_league_loads_with_our_cookies(client):
    """The `espn-api` path, used by the draft/box-score features that come later."""
    league = client.league

    assert league.settings.name
    assert len(league.teams) > 0
