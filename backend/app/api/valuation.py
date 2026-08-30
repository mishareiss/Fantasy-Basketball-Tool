"""The two opinionated things about the board — the age curve and the tiers — made inspectable.

Both are numbers we will argue about, so both have to be readable without opening the code.
Each endpoint answers the same two halves: what the active parameters are (whatever the
environment set them to, not what the defaults say), and what they actually come to.

They are deliberately read-only. Tuning happens through the DYNASTY_* and TIER_* settings — an
endpoint that wrote them would make a board's ranking depend on who called what and when.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.players import (
    HORIZON_CURRENT_YEAR,
    HORIZON_DYNASTY,
    TIERS_AUTO,
    ranked_board,
)
from app.config import get_settings
from app.db.session import get_db
from app.espn.sync import ESPN_SOURCE
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


class TierParamsModel(BaseModel):
    """The four tunables, as the running process has them."""

    gap_multiple: float
    min_size: int
    max_tiers: int
    pool: int


class TierRow(BaseModel):
    """One tier: its band, its size, and the cliff that opened it."""

    tier: int
    size: int
    value_high: float
    value_low: float
    # 1-based overall rank the tier starts at.
    start_rank: int
    # The value drop that opened this tier, and that drop as a multiple of the typical
    # (median) drop across the tiered pool. Null for tier 1 — nothing opened it but the top of
    # the board. The ratio is the significance ranking the max_tiers cap merges by.
    gap: float | None = None
    gap_ratio: float | None = None
    # The best player in the tier, so the structure can be eyeballed without the board beside
    # it. "Tier 3 starts at Jalen Williams" is the sentence this endpoint exists to produce.
    leader: str | None = None


class TiersResponse(BaseModel):
    """The tier structure over the tiered pool, and the arithmetic that produced it."""

    horizon: str
    source: str
    season: int
    params: TierParamsModel
    # The env var to set to move each parameter, so calibrating doesn't need the README.
    env_vars: dict[str, str]
    # The median gap across the tiered pool, and `gap_multiple` times it: a gap has to beat
    # the threshold to open a tier. Median, not mean, so the cliffs at the very top of the
    # board don't inflate the bar and swallow the ordinary breaks below them.
    typical_gap: float
    break_threshold: float
    # How many players were tiered (top TIER_POOL), out of how many are ranked at all.
    pool_size: int
    total_ranked: int
    tiers: list[TierRow]


@router.get("/tiers", response_model=TiersResponse)
def board_tiers(
    db: Session = Depends(get_db),
    horizon: str = Query(
        HORIZON_DYNASTY,
        description=f"Which horizon's values to tier: {HORIZON_CURRENT_YEAR!r} or "
        f"{HORIZON_DYNASTY!r}",
    ),
    source: str = Query(ESPN_SOURCE, description="Projection source to tier"),
    season: int | None = Query(
        None, description="Projection season; defaults to the newest stored for this source"
    ),
) -> TiersResponse:
    """Where the board breaks into tiers, and why there rather than a row up or down.

    This is `GET /players/board`'s tier column with the arithmetic shown: the same ranking,
    tiered by the same call, reported as structure instead of as a per-row number. Reading the
    `gap_ratio` column down the page is the fastest way to tell whether TIER_GAP_MULTIPLE is
    set anywhere near right — a board whose every break is barely over 2.0x is a board asking
    for a lower multiple.
    """
    board = ranked_board(db, source=source, season=season, horizon=horizon, tiers=TIERS_AUTO)
    # `tiers=TIERS_AUTO` above guarantees this; the check is for the type checker's benefit.
    assert board.tiering is not None
    tiering = board.tiering
    params = tiering.params

    return TiersResponse(
        horizon=board.horizon,
        source=board.source,
        season=board.season,
        params=TierParamsModel(
            gap_multiple=params.gap_multiple,
            min_size=params.min_size,
            max_tiers=params.max_tiers,
            pool=params.pool,
        ),
        env_vars={
            "gap_multiple": "TIER_GAP_MULTIPLE",
            "min_size": "TIER_MIN_SIZE",
            "max_tiers": "TIER_MAX",
            "pool": "TIER_POOL",
        },
        typical_gap=tiering.typical_gap,
        break_threshold=tiering.threshold,
        pool_size=tiering.pool_size,
        total_ranked=len(board.entries),
        tiers=[
            TierRow(
                tier=tier.tier,
                size=tier.size,
                value_high=tier.value_high,
                value_low=tier.value_low,
                start_rank=tier.start_index + 1,
                gap=tier.gap,
                gap_ratio=tier.gap_ratio,
                leader=board.entries[tier.start_index].player.full_name,
            )
            for tier in tiering.tiers
        ],
    )
