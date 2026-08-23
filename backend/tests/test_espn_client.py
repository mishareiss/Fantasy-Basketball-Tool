"""Credential handling. The app must boot without ESPN cookies; only a sync needs them."""

import pytest

from app.config import Settings
from app.espn.client import (
    ESPNClient,
    ESPNCredentialsError,
    credentials_available,
    require_credentials,
)

COMPLETE = {
    "espn_league_id": 12345,
    "espn_season": 2027,
    "espn_s2": "  cookie-value  ",
    "swid": "1234-5678",
}


def _settings(**overrides) -> Settings:
    # _env_file=None keeps the developer's real .env out of these assertions.
    return Settings(_env_file=None, **{**COMPLETE, **overrides})


def test_builds_credentials_and_braces_the_swid():
    credentials = require_credentials(_settings())

    assert credentials.espn_s2 == "cookie-value"
    assert credentials.swid == "{1234-5678}"
    assert credentials.cookies == {"espn_s2": "cookie-value", "SWID": "{1234-5678}"}


def test_leaves_an_already_braced_swid_alone():
    assert require_credentials(_settings(swid="{ABC}")).swid == "{ABC}"


@pytest.mark.parametrize(
    ("field", "env_name"),
    [
        ("espn_league_id", "ESPN_LEAGUE_ID"),
        ("espn_season", "ESPN_SEASON"),
        ("espn_s2", "ESPN_S2"),
        ("swid", "SWID"),
    ],
)
def test_names_the_missing_credential(field, env_name):
    with pytest.raises(ESPNCredentialsError, match=env_name):
        require_credentials(_settings(**{field: None}))


def test_credentials_available_reports_without_raising():
    assert credentials_available(_settings()) is True
    assert credentials_available(_settings(espn_s2=None)) is False


def test_client_builds_the_league_url():
    client = ESPNClient(require_credentials(_settings()))

    assert client.credentials.league_url.endswith("/seasons/2027/segments/0/leagues/12345")
    assert client.league_id == 12345
    assert client.season == 2027
