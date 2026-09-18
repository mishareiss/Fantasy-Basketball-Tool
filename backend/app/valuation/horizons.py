"""The board's two VALUE horizons, and the one number each of them ranks by.

These used to live in `app.api.players`, which is where the board first needed them. They sit
here now because they are a property of the *value engine*, not of an HTTP route: a horizon is
a lens over `PlayerValue` (`dynasty` = the projection through the age curve, `current_year` =
the projection as-is), and everything that ranks players — the board, the tier cutter, and now
the consensus source adapters in `app.ranking` — has to name them the same way.

`app.api.players` re-exports all four names, so nothing that already imported them from there
had to change.

NOT to be confused with `app.db.models.ranking.RANKING_HORIZONS` ('dynasty' | 'redraft'), which
tags an imported list. Two vocabularies on purpose; `app.ranking.horizons` maps between them.
"""

from app.valuation.engine import PlayerValue

HORIZON_CURRENT_YEAR = "current_year"
HORIZON_DYNASTY = "dynasty"
HORIZONS = (HORIZON_CURRENT_YEAR, HORIZON_DYNASTY)


def horizon_value(value: PlayerValue, horizon: str) -> float:
    """The one number a horizon ranks by — and therefore the one tiers are cut from.

    Single-sourced on purpose. The whole point of tiering the board is that the breaks fall in
    the values the board is ordered by; a second expression of "which number is this horizon"
    is a second board waiting to disagree with the first.
    """
    return value.dynasty if horizon == HORIZON_DYNASTY else value.current_year
