"""GET /valuation/curve and /valuation/tiers — the board's two opinions, inspectable."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db.session import get_db
from app.main import app
from app.valuation import SAMPLE_MAX_AGE, SAMPLE_MIN_AGE


@pytest.fixture
def api() -> TestClient:
    """No database override needed: the curve is settings, not stored data."""
    return TestClient(app)


@pytest.fixture
def board_api(db):
    """The tier endpoint, unlike the curve, reads a board — so it needs one."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_the_curve_reports_its_tunable_parameters(api):
    params = api.get("/valuation/curve").json()["params"]

    assert params == {
        "prime_start": 24,
        "prime_end": 27,
        "youth_bonus_per_year": 0.04,
        "decline_per_year": 0.05,
        "min_multiplier": 0.40,
    }


def test_the_curve_names_the_env_var_behind_each_parameter(api):
    body = api.get("/valuation/curve").json()

    assert set(body["env_vars"]) == set(body["params"])
    assert body["env_vars"]["decline_per_year"] == "DYNASTY_DECLINE_PER_YEAR"


def test_the_sample_table_shows_the_shape_age_by_age(api):
    body = api.get("/valuation/curve").json()

    assert (body["sample_min_age"], body["sample_max_age"]) == (SAMPLE_MIN_AGE, SAMPLE_MAX_AGE)
    ages = [row["age"] for row in body["sample"]]
    assert ages == list(range(SAMPLE_MIN_AGE, SAMPLE_MAX_AGE + 1))

    by_age = {row["age"]: row for row in body["sample"]}
    assert by_age[20]["multiplier"] == pytest.approx(1.16)
    assert by_age[20]["band"] == "youth"
    assert by_age[25]["multiplier"] == 1.0 and by_age[25]["band"] == "prime"
    assert by_age[32]["multiplier"] == pytest.approx(0.75)
    assert by_age[32]["band"] == "decline"
    assert by_age[40]["multiplier"] == 0.40 and by_age[40]["band"] == "floor"

    multipliers = [row["multiplier"] for row in body["sample"]]
    assert multipliers == sorted(multipliers, reverse=True)


def test_the_endpoint_reports_the_curve_the_board_is_actually_using(api):
    """Its whole job: read back what the environment set, not what the defaults say."""
    settings = get_settings()
    original = settings.dynasty_prime_end
    try:
        settings.dynasty_prime_end = 30
        body = api.get("/valuation/curve").json()
    finally:
        settings.dynasty_prime_end = original

    assert body["params"]["prime_end"] == 30
    assert {row["age"]: row["multiplier"] for row in body["sample"]}[29] == 1.0
    assert api.get("/valuation/curve").json()["params"]["prime_end"] == original


# --- GET /valuation/tiers ------------------------------------------------------------------


def test_the_tiers_endpoint_reports_its_tunable_parameters(board_api, aged):
    body = board_api.get("/valuation/tiers").json()

    assert body["params"] == {"gap_multiple": 2.0, "min_size": 2, "max_tiers": 15, "pool": 150}
    assert set(body["env_vars"]) == set(body["params"])
    assert body["env_vars"]["gap_multiple"] == "TIER_GAP_MULTIPLE"


def test_the_tiers_endpoint_shows_the_arithmetic_behind_the_breaks(board_api, aged):
    """The median drop, and the bar a drop has to clear to become a tier."""
    body = board_api.get("/valuation/tiers").json()

    assert body["typical_gap"] > 0
    assert body["break_threshold"] == pytest.approx(
        body["params"]["gap_multiple"] * body["typical_gap"], abs=1e-3
    )
    assert body["pool_size"] == body["total_ranked"], "the fixture board fits inside TIER_POOL"


def test_every_tier_carries_its_size_band_and_the_gap_that_opened_it(board_api, aged):
    body = board_api.get("/valuation/tiers").json()
    tiers = body["tiers"]

    assert [tier["tier"] for tier in tiers] == list(range(1, len(tiers) + 1))
    assert sum(tier["size"] for tier in tiers) == body["pool_size"]
    assert tiers[0]["start_rank"] == 1
    assert tiers[0]["gap"] is None and tiers[0]["gap_ratio"] is None
    for tier in tiers:
        assert tier["value_high"] >= tier["value_low"]
        assert tier["leader"], "a tier without a name in it can't be eyeballed"
    for tier in tiers[1:]:
        assert tier["gap"] > body["break_threshold"]
        assert tier["gap_ratio"] == pytest.approx(tier["gap"] / body["typical_gap"], abs=1e-3)
    # ...and they tile the pool with no overlap and no hole.
    assert [tier["start_rank"] for tier in tiers[1:]] == [
        tiers[index]["start_rank"] + tiers[index]["size"] for index in range(len(tiers) - 1)
    ]


def test_the_tier_structure_is_the_board_column_with_its_workings_shown(board_api, aged):
    """Two views of one computation — the endpoint would be worthless if they could differ."""
    structure = board_api.get("/valuation/tiers?horizon=dynasty").json()
    board = board_api.get("/players/board?limit=1000&horizon=dynasty").json()

    assert [tier["tier"] for tier in structure["tiers"]] == [
        row["tier"] for row in board["tier_summary"]
    ]
    for tier, summary in zip(structure["tiers"], board["tier_summary"], strict=True):
        assert (tier["size"], tier["start_rank"], tier["gap"]) == (
            summary["size"],
            summary["start_rank"],
            summary["gap"],
        )
        assert tier["leader"] == board["players"][tier["start_rank"] - 1]["name"]


def test_the_horizon_picks_which_values_get_tiered(board_api, aged):
    dynasty = board_api.get("/valuation/tiers?horizon=dynasty").json()
    current = board_api.get("/valuation/tiers?horizon=current_year").json()

    assert (dynasty["horizon"], current["horizon"]) == ("dynasty", "current_year")
    assert dynasty["total_ranked"] == current["total_ranked"]
    assert dynasty["tiers"] != current["tiers"]
    assert dynasty["tiers"][0]["leader"] != current["tiers"][0]["leader"]


def test_the_tiers_endpoint_is_dynasty_by_default(board_api, aged):
    assert (
        board_api.get("/valuation/tiers").json()
        == board_api.get("/valuation/tiers?horizon=dynasty").json()
    )


def test_the_tiers_endpoint_reports_the_parameters_the_board_is_actually_using(board_api, aged):
    """Its whole job: read back what the environment set, not what the defaults say."""
    settings = get_settings()
    before = board_api.get("/valuation/tiers").json()
    original = settings.tier_gap_multiple
    try:
        settings.tier_gap_multiple = 6.0
        body = board_api.get("/valuation/tiers").json()
    finally:
        settings.tier_gap_multiple = original

    assert body["params"]["gap_multiple"] == 6.0
    assert body["break_threshold"] > before["break_threshold"]
    assert len(body["tiers"]) < len(before["tiers"])
    assert board_api.get("/valuation/tiers").json() == before


def test_an_unknown_horizon_lists_the_ones_that_work(board_api, aged):
    response = board_api.get("/valuation/tiers?horizon=next_decade")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "current_year" in detail and "dynasty" in detail


def test_an_unsynced_database_says_so_instead_of_tiering_nothing(board_api, db):
    response = board_api.get("/valuation/tiers")

    assert response.status_code == 404
    assert "sync" in response.json()["detail"].lower()
