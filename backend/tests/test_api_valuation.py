"""GET /valuation/curve — the dynasty adjustment, inspectable without reading the code."""

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.valuation import SAMPLE_MAX_AGE, SAMPLE_MIN_AGE


@pytest.fixture
def api() -> TestClient:
    """No database override needed: the curve is settings, not stored data."""
    return TestClient(app)


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
