"""The one place the board's horizon is translated into a ranking set's tag.

There are two horizon vocabularies in this codebase, deliberately (see the comment at the top
of `app.db.models.ranking`):

* the BOARD's value horizon — `current_year` | `dynasty` (`app.valuation.horizons`). It names a
  *computed lens over production*: dynasty is the projection through the age curve, current-year
  is the projection as-is. Value sources speak this directly, because a per-player number can be
  aged.
* a RANKING SET's tag — `dynasty` | `redraft` (`app.db.models.ranking.RANKING_HORIZONS`). It
  names *what an imported list already is*. A rank-only list carries no stats, so nothing
  downstream can age-adjust it; the only thing we can do is ask whether the list was written
  about the same question the board is currently asking.

Mapping between them is a one-liner, which is exactly why it has to live in one place: written
inline it would be written four times (source discovery, source loading, the `/sources` route,
the `/board/consensus` route) and one of those four would eventually say something different.

The mapping, and the reasoning behind each half:

* `dynasty` -> `dynasty`. Same word, same question.
* `current_year` -> `redraft`. NOT the same word, and that is the whole point. The board's
  win-now horizon asks "who helps me this season"; the list that answers that question is the
  one its publisher tagged `redraft`. A dynasty top-200 under a win-now board would be an
  answer to a question nobody asked, so it drops out of the eligible sources entirely rather
  than being shown with a caveat.
"""

from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.db.models.ranking import HORIZON_REDRAFT as TAG_REDRAFT
from app.valuation import HORIZON_CURRENT_YEAR, HORIZON_DYNASTY, HORIZONS

# Board horizon -> the ranking-set tag whose lists answer that horizon's question.
RANKING_TAG_BY_HORIZON: dict[str, str] = {
    HORIZON_DYNASTY: TAG_DYNASTY,
    HORIZON_CURRENT_YEAR: TAG_REDRAFT,
}


class UnknownHorizon(ValueError):
    """A horizon that is not one of the board's two. Routes turn this into a 400."""


def ranking_tag_for_horizon(horizon: str) -> str:
    """Which `RankingSet.horizon` tag a board horizon accepts rank-only lists from.

    Raises `UnknownHorizon` rather than guessing: a typo'd horizon silently falling back to
    dynasty would show a board built from lists that answer the other question.
    """
    try:
        return RANKING_TAG_BY_HORIZON[horizon]
    except KeyError:
        raise UnknownHorizon(
            f"Unknown horizon {horizon!r}; supported: "
            + ", ".join(repr(name) for name in HORIZONS)
            + "."
        ) from None
