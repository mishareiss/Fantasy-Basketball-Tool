"""The dynasty curve, made inspectable.

The age adjustment is the most opinionated number on the board and the one we will argue about
most, so it has to be readable without opening the code. This endpoint answers both halves of
that: what the active parameters are (whatever the environment set them to, not what the
defaults say), and what they actually do to a player of each age.

It is deliberately read-only. Tuning happens through the DYNASTY_* settings — an endpoint that
wrote them would make a board's ranking depend on who called what and when.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.valuation import SAMPLE_MAX_AGE, SAMPLE_MIN_AGE, sample_table

router = APIRouter(prefix="/valuation", tags=["valuation"])


class CurveParams(BaseModel):
    """The five tunables, as the running process has them."""

    prime_start: int
    prime_end: int
    youth_bonus_per_year: float
    decline_per_year: float
    min_multiplier: float


class CurveSampleRow(BaseModel):
    """One age, what dynasty value multiplies by at it, and which segment did it."""

    age: int
    multiplier: float
    # 'youth' | 'prime' | 'decline' | 'floor' — the shape, in words.
    band: str


class CurveResponse(BaseModel):
    """The active curve: its parameters and what they come to, age by age."""

    params: CurveParams
    # The env var to set to move each parameter, so calibrating doesn't need the README.
    env_vars: dict[str, str]
    sample_min_age: int
    sample_max_age: int
    sample: list[CurveSampleRow]


@router.get("/curve", response_model=CurveResponse)
def dynasty_curve() -> CurveResponse:
    """The age/longevity curve behind every `dynasty_value` on the board."""
    curve = get_settings().dynasty_curve()
    return CurveResponse(
        params=CurveParams(
            prime_start=curve.prime_start,
            prime_end=curve.prime_end,
            youth_bonus_per_year=curve.youth_bonus_per_year,
            decline_per_year=curve.decline_per_year,
            min_multiplier=curve.min_multiplier,
        ),
        env_vars={
            "prime_start": "DYNASTY_PRIME_START",
            "prime_end": "DYNASTY_PRIME_END",
            "youth_bonus_per_year": "DYNASTY_YOUTH_BONUS_PER_YEAR",
            "decline_per_year": "DYNASTY_DECLINE_PER_YEAR",
            "min_multiplier": "DYNASTY_MIN_MULTIPLIER",
        },
        sample_min_age=SAMPLE_MIN_AGE,
        sample_max_age=SAMPLE_MAX_AGE,
        sample=[
            CurveSampleRow(age=point.age, multiplier=point.multiplier, band=point.band)
            for point in sample_table(curve)
        ],
    )
