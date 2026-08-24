"""The age sync: match nba.com's roster to our players, fetch birthdates, derive ages.

Three passes, in this order, because each one is cheap only if the one before it ran:

1. **Match** (offline, instant). Every name on nba.com's bundled roster is resolved against
   our canonical players and the confident ones are recorded as `PlayerAlias` rows. A
   recorded alias short-circuits every later run, so this pass converges.
2. **Fetch** (one HTTP call per player, so: incremental). Only players that have an nba alias
   *and* no birthdate are fetched, paced, retried, and committed in batches — kill it halfway
   and the next run picks up where it stopped.
3. **Derive**. `Player.age` is recomputed from `Player.birthdate` at `Settings.age_as_of`.

Birthdate is the source of truth and age is a cached derivative: change the as-of date and
pass 3 alone rebuilds every age, with no network involved.
"""

import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ages.nba_source import (
    DEFAULT_DELAY,
    NBA_SOURCE,
    NbaApiError,
    NbaPlayer,
    fetch_birthdate,
    static_players,
)
from app.config import get_settings
from app.db.models import Player, PlayerAlias, Projection
from app.matching import (
    METHOD_ALIAS,
    METHOD_AMBIGUOUS,
    METHOD_FUZZY,
    MatchResult,
    PlayerMatcher,
    build_matcher,
)
from app.matching.store import record_alias

# How many birthdate fetches to commit at a time. Small enough that a killed run loses almost
# nothing, large enough that we're not doing a transaction per HTTP call.
COMMIT_EVERY = 20

# How many names to keep in the summary's worklists. The full list is a query away; this is
# meant to be read in a terminal.
SAMPLE_SIZE = 25

BirthdateFetcher = Callable[[int], date | None]

# Methods that involve guessing rather than reading a name off the page.
_GUESSED = (METHOD_FUZZY, METHOD_AMBIGUOUS)


@dataclass
class AgeSyncSummary:
    """What one age sync did. Printed by the CLI, returned by the endpoint."""

    age_as_of: date

    # Pass 1, from nba.com's side. `nba_unmatched` is expected to be large and is not a
    # problem: the static roster carries every player since 1946, and our pool is ~1,000
    # current ones.
    nba_roster: int = 0
    nba_matched: int = 0
    nba_ambiguous: int = 0
    nba_unmatched: int = 0
    aliases_created: int = 0
    aliases_existing: int = 0

    # Pass 2 and 3, from our side — this is the half that says whether the board is complete.
    players_total: int = 0
    players_with_alias: int = 0
    players_without_alias: int = 0
    birthdates_fetched: int = 0
    birthdates_absent: int = 0
    birthdates_failed: int = 0
    birthdates_pending: int = 0
    ages_set: int = 0
    players_with_age: int = 0
    players_missing_age: int = 0

    # The manual-alias worklist: our players nothing on nba.com resolved to.
    unresolved_players: list[str] = field(default_factory=list)
    ambiguous_names: list[str] = field(default_factory=list)


def compute_age(birthdate: date, as_of: date) -> int:
    """Whole years old on `as_of`. No leap-day special case — Feb 29 turns on Mar 1."""
    had_birthday = (as_of.month, as_of.day) >= (birthdate.month, birthdate.day)
    return as_of.year - birthdate.year - (0 if had_birthday else 1)


def _claim_rank(nba_player: NbaPlayer, result: MatchResult) -> tuple:
    """How strong one nba player's claim on a canonical player is, best last.

    Two nba.com entries can normalize to the same name — "Gary Payton" (retired) and "Gary
    Payton II" (active) both reduce to `gary payton`, and only one of them is our player.
    Ranking the claims and keeping the best one is what stops us storing the father's
    birthdate. A recorded alias outranks everything, then the active player, then the score.
    """
    return (
        result.method == METHOD_ALIAS,
        nba_player.is_active,
        result.confidence,
        nba_player.nba_id,
    )


def match_nba_roster(
    matcher: PlayerMatcher,
    nba_players: Sequence[NbaPlayer],
    summary: AgeSyncSummary,
) -> dict[int, tuple[NbaPlayer, MatchResult]]:
    """Resolve every nba.com name to one of our players. Pure: writes nothing.

    Returns the winning claim per canonical player id, so each of our players ends up with at
    most one nba alias.
    """
    summary.nba_roster = len(nba_players)
    claims: dict[int, tuple[NbaPlayer, MatchResult]] = {}

    for nba_player in nba_players:
        result = matcher.match(
            nba_player.full_name,
            source=NBA_SOURCE,
            source_id=nba_player.source_id,
        )
        if result.method in _GUESSED and not nba_player.is_active:
            # Four fifths of the static roster retired before we were watching, and letting
            # them guess is all downside. Retired "Mike Brown" scores 0.95 against our rookie
            # Mikel Brown Jr., and retired "Alvin Williams" muddies two current Williamses
            # into an ambiguity. An exact or normalized name still resolves — the 2005 Marcus
            # Williams in our deep tail really is that Marcus Williams — but nothing is
            # *guessed* at from a player nba.com says is no longer active.
            summary.nba_unmatched += 1
            continue
        if result.method == METHOD_AMBIGUOUS:
            summary.nba_ambiguous += 1
            if len(summary.ambiguous_names) < SAMPLE_SIZE:
                names = ", ".join(candidate.full_name for candidate in result.candidates)
                summary.ambiguous_names.append(f"{nba_player.full_name} -> {names}")
            continue
        if not result.matched:
            summary.nba_unmatched += 1
            continue

        incumbent = claims.get(result.player_id)
        if incumbent is None or _claim_rank(nba_player, result) > _claim_rank(*incumbent):
            claims[result.player_id] = (nba_player, result)

    summary.nba_matched = len(claims)
    return claims


def record_nba_aliases(
    db: Session,
    claims: dict[int, tuple[NbaPlayer, MatchResult]],
    summary: AgeSyncSummary,
) -> None:
    """Persist each winning claim as a `PlayerAlias`. Idempotent by (source, source_name)."""
    for player_id, (nba_player, result) in claims.items():
        _, created = record_alias(
            db,
            source=NBA_SOURCE,
            source_name=nba_player.full_name,
            source_id=nba_player.source_id,
            player_id=player_id,
            confidence=result.confidence,
            match_method=result.method,
        )
        if created:
            summary.aliases_created += 1
        else:
            summary.aliases_existing += 1
    db.flush()


def players_needing_birthdate(db: Session, *, refresh: bool = False) -> list[tuple[Player, str]]:
    """Players with an nba alias whose birthdate we still need, best players first.

    The ordering earns its keep when a run is cut short with `--limit`: the players whose ages
    move the board most get fetched first, not whoever happens to have the lowest ESPN id.
    """
    query = (
        select(Player, PlayerAlias.source_id)
        .join(PlayerAlias, PlayerAlias.player_id == Player.espn_player_id)
        .outerjoin(Projection, Projection.player_id == Player.espn_player_id)
        .where(PlayerAlias.source == NBA_SOURCE, PlayerAlias.source_id.is_not(None))
        .order_by(Projection.fantasy_points_per_game.desc().nulls_last(), Player.espn_player_id)
    )
    if not refresh:
        query = query.where(Player.birthdate.is_(None))

    seen: set[int] = set()
    rows: list[tuple[Player, str]] = []
    for player, source_id in db.execute(query):
        # A player can carry several projections (one per season); we want him once.
        if player.espn_player_id not in seen:
            seen.add(player.espn_player_id)
            rows.append((player, source_id))
    return rows


def fetch_birthdates(
    db: Session,
    *,
    refresh: bool = False,
    limit: int | None = None,
    delay: float = DEFAULT_DELAY,
    fetch: BirthdateFetcher = fetch_birthdate,
    sleep: Callable[[float], None] = time.sleep,
    summary: AgeSyncSummary,
) -> None:
    """Fetch the birthdates we're missing, politely, committing as we go.

    Stops early on `NbaApiError` — that means nba.com is refusing us, and grinding through
    another 900 players would only make that worse. Everything already fetched is committed.
    """
    pending = players_needing_birthdate(db, refresh=refresh)
    todo = pending[:limit] if limit is not None else pending
    summary.birthdates_pending = len(pending) - len(todo)

    for index, (player, source_id) in enumerate(todo):
        if index:
            sleep(delay)
        try:
            birthdate = fetch(int(source_id))
        except NbaApiError:
            summary.birthdates_failed += 1
            summary.birthdates_pending += len(todo) - index - 1
            break

        if birthdate is None:
            # nba.com knows the player but has no birthdate on file — not an error, and not
            # worth retrying every run either; it just stays on the missing-age list.
            summary.birthdates_absent += 1
            continue

        player.birthdate = birthdate
        summary.birthdates_fetched += 1

        if summary.birthdates_fetched % COMMIT_EVERY == 0:
            db.commit()

    db.commit()


def recompute_ages(db: Session, as_of: date) -> int:
    """Rebuild `Player.age` from `Player.birthdate` at `as_of`. Returns how many changed."""
    changed = 0
    for player in db.scalars(select(Player)):
        age = compute_age(player.birthdate, as_of) if player.birthdate else None
        if player.age != age:
            player.age = age
            changed += 1
    db.flush()
    return changed


def _fill_coverage(db: Session, summary: AgeSyncSummary) -> None:
    """The numbers that say whether the board is complete, plus the manual-alias worklist."""
    aliased = set(db.scalars(select(PlayerAlias.player_id).where(PlayerAlias.source == NBA_SOURCE)))
    value: dict[int, float] = {}
    for player_id, per_game in db.execute(
        select(Projection.player_id, Projection.fantasy_points_per_game)
    ):
        value[player_id] = max(value.get(player_id, 0.0), per_game)

    unresolved: list[tuple[float, str]] = []
    for player in db.scalars(select(Player)):
        summary.players_total += 1
        if player.espn_player_id in aliased:
            summary.players_with_alias += 1
        else:
            summary.players_without_alias += 1
            unresolved.append((-value.get(player.espn_player_id, 0.0), player.full_name))
        if player.age is None:
            summary.players_missing_age += 1
        else:
            summary.players_with_age += 1

    # Most valuable first: a missing age on a projected starter costs more than one on a
    # two-way contract, and this list is meant to be worked from the top down.
    summary.unresolved_players = [name for _, name in sorted(unresolved)][:SAMPLE_SIZE]


def sync_ages(
    db: Session,
    *,
    refresh: bool = False,
    limit: int | None = None,
    delay: float = DEFAULT_DELAY,
    as_of: date | None = None,
    nba_players: Iterable[NbaPlayer] | None = None,
    fetch: BirthdateFetcher = fetch_birthdate,
    sleep: Callable[[float], None] = time.sleep,
) -> AgeSyncSummary:
    """Match, fetch, derive. Idempotent and resumable; commits on the way through.

    `nba_players` and `fetch` are injectable so the tests can run the whole thing against
    recorded fixtures with no network.
    """
    as_of = as_of or get_settings().resolved_age_as_of()
    summary = AgeSyncSummary(age_as_of=as_of)

    roster = list(nba_players) if nba_players is not None else static_players()
    matcher = build_matcher(db, source=NBA_SOURCE)
    record_nba_aliases(db, match_nba_roster(matcher, roster, summary), summary)
    db.commit()

    fetch_birthdates(
        db, refresh=refresh, limit=limit, delay=delay, fetch=fetch, sleep=sleep, summary=summary
    )

    summary.ages_set = recompute_ages(db, as_of)
    db.commit()

    _fill_coverage(db, summary)
    return summary
