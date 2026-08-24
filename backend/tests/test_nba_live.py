"""Live nba.com checks. Deselected by default, so `make test` stays offline and green.

Run them deliberately with `make test-nba` (`uv run pytest -m nbaapi`). They are the canary
for the two things the recorded fixtures cannot see: nba.com renaming a column, and nba.com
deciding it no longer wants to talk to us.

Kept to a handful of calls on purpose — the endpoint is undocumented, unversioned, and
somebody else's server.
"""

import pytest

from app.ages import (
    BIRTHDATE_COLUMN,
    NbaApiError,
    compute_age,
    fetch_birthdate,
    fetch_common_player_info,
    nba_api_available,
    static_players,
)
from app.matching import normalize_name
from tests.conftest import AGE_AS_OF

pytestmark = [
    pytest.mark.nbaapi,
    pytest.mark.skipif(not nba_api_available(), reason="nba_api is not installed"),
]

# Bam Adebayo: on a roster, uncontroversially spelled, and old enough that his birthdate is
# not going to be revised.
BAM = 1628389
BAM_BIRTHDATE = "1997-07-18"


def test_the_static_roster_is_offline_and_plausible():
    """No network at all — this one passes on a plane, and would fail if that ever changed."""
    roster = static_players()

    assert len(roster) > 4000, "the bundled roster looks truncated"
    assert sum(player.is_active for player in roster) > 300, "nobody is active?"
    assert all(player.nba_id and player.full_name for player in roster)


def test_the_static_roster_still_spells_names_the_way_we_expect():
    """Accents in, and the normalizer folding them out, is what the whole match depends on."""
    by_normalized = {normalize_name(player.full_name): player for player in static_players()}

    assert by_normalized["nikola jokic"].full_name == "Nikola Jokić"
    assert by_normalized["luka doncic"].full_name == "Luka Dončić"


def test_common_player_info_still_returns_a_birthdate():
    """The one column the age sync reads. If nba.com renames it, this is where we find out."""
    try:
        payload = fetch_common_player_info(BAM, timeout=30)
    except NbaApiError as exc:
        pytest.skip(f"nba.com unreachable: {exc}")

    rows = payload["CommonPlayerInfo"]
    assert rows and BIRTHDATE_COLUMN in rows[0]
    assert str(rows[0][BIRTHDATE_COLUMN]).startswith(BAM_BIRTHDATE)


def test_a_live_birthdate_produces_the_age_we_expect():
    try:
        birthdate = fetch_birthdate(BAM, timeout=30)
    except NbaApiError as exc:
        pytest.skip(f"nba.com unreachable: {exc}")

    assert birthdate is not None
    assert birthdate.isoformat() == BAM_BIRTHDATE
    assert compute_age(birthdate, AGE_AS_OF) == 29


def test_an_id_nba_com_does_not_know_fails_loudly():
    """A hand-typed alias with a bad id should raise, not silently store no birthdate."""
    with pytest.raises(NbaApiError):
        fetch_birthdate(1, timeout=10, retries=0)
