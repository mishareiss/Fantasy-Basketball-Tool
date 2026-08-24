"""Source-agnostic player matching: normalize a name, resolve it to a canonical `Player`.

Every external source (nba.com, a projections CSV, an ADP paste, a props feed) names players
differently. This package is the one place that decides which of our ESPN-keyed players a
foreign name means, and `PlayerAlias` is where a decision is remembered so it is never
re-guessed.
"""

from app.matching.matcher import (
    DEFAULT_MARGIN,
    DEFAULT_THRESHOLD,
    METHOD_ALIAS,
    METHOD_AMBIGUOUS,
    METHOD_EXACT,
    METHOD_FUZZY,
    METHOD_NORMALIZED,
    METHOD_UNMATCHED,
    AliasIndex,
    CanonicalPlayer,
    MatchCandidate,
    MatchResult,
    PlayerMatcher,
    similarity,
)
from app.matching.names import (
    GENERATIONAL_SUFFIXES,
    last_token,
    name_tokens,
    normalize_name,
    strip_accents,
    strip_suffixes,
)
from app.matching.store import (
    MANUAL_SOURCE,
    alias_index,
    build_matcher,
    canonical_players,
    find_alias,
    record_alias,
    record_match,
)

__all__ = [
    "DEFAULT_MARGIN",
    "DEFAULT_THRESHOLD",
    "GENERATIONAL_SUFFIXES",
    "MANUAL_SOURCE",
    "METHOD_ALIAS",
    "METHOD_AMBIGUOUS",
    "METHOD_EXACT",
    "METHOD_FUZZY",
    "METHOD_NORMALIZED",
    "METHOD_UNMATCHED",
    "AliasIndex",
    "CanonicalPlayer",
    "MatchCandidate",
    "MatchResult",
    "PlayerMatcher",
    "alias_index",
    "build_matcher",
    "canonical_players",
    "find_alias",
    "last_token",
    "name_tokens",
    "normalize_name",
    "record_alias",
    "record_match",
    "similarity",
    "strip_accents",
    "strip_suffixes",
]
