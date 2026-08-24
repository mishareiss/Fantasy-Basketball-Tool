"""The database side of matching: build a matcher from our players, record a resolved alias.

Kept apart from `matcher.py` so the matcher itself stays a pure, sessionless object that a
test can construct from a literal list.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerAlias
from app.matching.matcher import (
    METHOD_ALIAS,
    AliasIndex,
    CanonicalPlayer,
    MatchResult,
    PlayerMatcher,
)

# Aliases we made ourselves, for a match nothing automatic could reach.
MANUAL_SOURCE = "manual"


def canonical_players(db: Session) -> list[CanonicalPlayer]:
    """Every `Player` row, reduced to the fields matching needs."""
    return [
        CanonicalPlayer(
            player_id=player.espn_player_id,
            full_name=player.full_name,
            first_name=player.first_name,
            last_name=player.last_name,
            nba_team=player.nba_team,
            positions=tuple(player.positions or ()),
        )
        for player in db.scalars(select(Player).order_by(Player.espn_player_id))
    ]


def alias_index(db: Session, *, source: str | None = None) -> AliasIndex:
    """Load recorded aliases into the index the matcher consults first."""
    query = select(PlayerAlias)
    if source is not None:
        query = query.where(PlayerAlias.source == source)

    index = AliasIndex()
    for alias in db.scalars(query):
        index.add(alias.source, alias.source_name, alias.source_id, alias.player_id)
    return index


def build_matcher(db: Session, *, source: str | None = None, **kwargs) -> PlayerMatcher:
    """A matcher over our canonical players, primed with the aliases already recorded."""
    return PlayerMatcher(canonical_players(db), aliases=alias_index(db, source=source), **kwargs)


def find_alias(db: Session, source: str, source_name: str) -> PlayerAlias | None:
    """The recorded alias for this (source, name), if any — the unique key on the table."""
    return db.scalar(
        select(PlayerAlias).where(
            PlayerAlias.source == source, PlayerAlias.source_name == source_name
        )
    )


def record_alias(
    db: Session,
    *,
    source: str,
    source_name: str,
    player_id: int,
    source_id: str | None = None,
    confidence: float | None = None,
    match_method: str | None = None,
    restate_provenance: bool = False,
) -> tuple[PlayerAlias, bool]:
    """Upsert one alias by its natural key. Returns `(alias, created)`.

    Provenance is written once, when the alias is created, and left alone afterwards. That is
    the whole point of storing it: `match_method` answers "how did we first decide this?", and
    a re-run must not answer it with "because the alias said so" — which is exactly what would
    happen, since every later run resolves this name through the alias it just wrote.

    `restate_provenance` overrides that, for a caller whose new answer is genuinely better than
    the stored one: a human re-pointing the alias by hand.

    Hand-written rather than an `ON CONFLICT`, so the same code runs on the SQLite the tests
    use and the Postgres everything else uses.
    """
    alias = find_alias(db, source, source_name)
    if alias is None:
        alias = PlayerAlias(
            player_id=player_id,
            source=source,
            source_name=source_name,
            source_id=source_id,
            confidence=confidence,
            match_method=match_method,
        )
        db.add(alias)
        db.flush()
        return alias, True

    repointed = alias.player_id != player_id
    alias.player_id = player_id
    if source_id is not None:
        alias.source_id = source_id

    # `alias` as a method is circular — it means "we matched this because this row exists" —
    # so it is never provenance, whatever the caller passes.
    informative = match_method not in (None, METHOD_ALIAS)
    # An alias written before this column existed has no provenance to protect, and one
    # pointed at a different player no longer describes how *this* match was made.
    if informative and (restate_provenance or repointed or alias.match_method is None):
        alias.confidence = confidence
        alias.match_method = match_method
    db.flush()
    return alias, False


def record_match(db: Session, source: str, result: MatchResult, *, source_id: str | None = None):
    """Persist a `MatchResult` as an alias, if it resolved. Returns `(alias, created)` or None."""
    if not result.matched:
        return None
    return record_alias(
        db,
        source=source,
        source_name=result.source_name,
        player_id=result.player_id,
        source_id=source_id,
        confidence=result.confidence,
        match_method=result.method,
    )
