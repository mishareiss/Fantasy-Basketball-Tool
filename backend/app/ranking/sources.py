"""Every way we hold an opinion about who is better, behind one interface.

The board used to have exactly one opinion on it: ESPN's projection, priced under our scoring.
The point of a consensus board is that our opinions come in three storage shapes and none of
them should have to be special-cased above this module:

* **value sources** carry a per-player NUMBER (`Projection` today; market lines later). They can
  be aged, so the board's horizon applies to them directly: `dynasty` ranks by the projection
  through the age curve, `current_year` by the projection as-is. One source per distinct
  `Projection.source`.
* **rank sources** carry an ORDER and nothing underneath it. An imported `RankingSet` is one,
  and because a rank-only list has no stats to age-adjust it declares at import which question
  it answers — so it is eligible only under the board horizon that maps to its tag (see
  `app.ranking.horizons`). ESPN's ADP is the other: a market's order, lower being better.

What every adapter returns is the same two numbers per player, which is what makes them
averageable in `app.ranking.consensus`:

* **rank** — where this source puts him. For a `RankingSet` that is the rank the source
  PUBLISHED (`RankingEntry.rank`, gaps and all: see the model docstring — re-deriving it would
  quietly disagree with the board it came from). For a value or ADP source there is no
  published rank, so it is a competition rank over the source's own ordering: ties share a
  number (1, 2, 2, 4), which matters because ESPN floors every undrafted player at the same
  ADP and several hundred of them would otherwise be handed a fake pecking order by name.
* **percentile** — that rank as a position in the SHARED DRAFTABLE POOL, 100 at the top and 0
  at the bottom of the pool. One denominator for every source, which is what lets a 449-name
  dynasty list and a 1,095-name ADP table be read off the same 0-100 scale and, more to the
  point, lets a per-player SPREAD between them mean something. Two sources that both place a
  player 449th agree, and their percentiles say so, however long their lists are.

  The consequence, stated plainly because it decides what the rank/percentile toggle is for:
  percentile is an affine restatement of rank over one shared denominator, so for a player
  every selected source ranks, the two consensus methods produce the same ORDER. They diverge
  exactly where coverage does — a player two sources rank and a third doesn't — and the
  percentile scale is the readable one for the spread colouring. Normalising each source over
  its OWN length instead would make the toggle re-order the board, but it would also have
  Dizzle's 449th (his last name) and ESPN's 449th (of 1,095) look like violent disagreement
  when they are the same opinion, so it is not what we do.

THE SHARED DRAFTABLE POOL is every player ranked by at least one source AVAILABLE under this
horizon — not by the selected ones. Ticking a source's box must not silently restate every
other source's percentiles, so the denominator is a property of (database, horizon) alone.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AdpEntry, Player, Projection, RankingEntry, RankingSet
from app.espn.sync import SEASON_PROJECTION_KIND
from app.ranking.horizons import ranking_tag_for_horizon
from app.valuation import horizon_value

# The three storage shapes an opinion arrives in. `kind` is on every source so a client can
# group the chips ("projections", "markets", "imported boards") without parsing the id.
KIND_PROJECTION = "projection"
KIND_ADP = "adp"
KIND_RANKING = "ranking"
KINDS = (KIND_PROJECTION, KIND_ADP, KIND_RANKING)


@dataclass(frozen=True)
class SourceSpec:
    """What a source IS, without its rows — what `GET /sources` lists.

    `id` is the stable handle a client selects with. It is built from what identifies the
    source and NOT from the season, so a fresh sync that lands next season's projection does
    not invalidate a saved selection; a `RankingSet` is keyed by its row id, which is already
    stable across a re-import (the ingest replaces a set's entries, not the set).
    """

    id: str
    label: str
    # One of KINDS.
    kind: str
    # The publisher: 'espn', 'Dizzle Dynasty'. Same vocabulary across all three kinds.
    source: str
    # Which season's rows were read. None only if a kind ever stops being season-keyed.
    season: int | None = None
    # The rank-set TAG ('dynasty' | 'redraft') for a ranking source; None for the others,
    # which derive both horizons from production rather than declaring one.
    ranking_horizon: str | None = None


@dataclass(frozen=True)
class Placement:
    """One player's place on one source's board."""

    player_id: int
    # Where the source puts him: published for a ranking set, competition-ranked otherwise.
    rank: int
    # `rank` as a position in the shared draftable pool, 100 best.
    percentile: float


@dataclass(frozen=True)
class RankingSource:
    """A source and its placements, best first."""

    spec: SourceSpec
    placements: tuple[Placement, ...]

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def player_count(self) -> int:
        """How many pool players this source has an opinion about — its coverage."""
        return len(self.placements)

    def by_player(self) -> dict[int, Placement]:
        return {placement.player_id: placement for placement in self.placements}


@dataclass(frozen=True)
class SourceCatalog:
    """Every source available under one horizon, already placed over one shared pool."""

    horizon: str
    # The rank-set tag this horizon accepts imported lists from.
    ranking_horizon: str
    # Every player at least one available source ranks — the percentile denominator.
    pool: frozenset[int]
    sources: tuple[RankingSource, ...]

    @property
    def pool_size(self) -> int:
        return len(self.pool)

    def get(self, source_id: str) -> RankingSource | None:
        return next((source for source in self.sources if source.id == source_id), None)

    def select(self, ids: Sequence[str]) -> tuple[list[RankingSource], list[str]]:
        """Resolve requested ids to sources, in the order asked for.

        Returns the sources found and the ids that matched nothing, so a route can 400 on a
        typo rather than quietly averaging fewer sources than the caller believes it asked for.
        """
        found: list[RankingSource] = []
        unknown: list[str] = []
        for source_id in ids:
            source = self.get(source_id)
            if source is None:
                unknown.append(source_id)
            elif source not in found:
                found.append(source)
        return found, unknown


def percentile_for(rank: int, pool_size: int) -> float:
    """A rank's position in the shared pool: 100 at rank 1, 0 at the bottom of the pool.

    Clamped, because a published rank can point past the pool — a source that numbers 1..250
    with gaps ends above 250, and the pool it is being read against is whatever the database
    happens to hold. A rank off the bottom is worth 0, never a negative percentile.
    """
    if pool_size <= 1:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (pool_size - rank) / (pool_size - 1)))


def _competition_ranked(ordered: Sequence[tuple[int, float]]) -> list[tuple[int, int]]:
    """Best-first `(player_id, ordering key)` -> `(player_id, rank)`, ties sharing a rank.

    Standard competition ranking (1, 2, 2, 4). Equal keys are equal opinions, and handing them
    different ranks would invent disagreement out of the tie-break — which is not hypothetical:
    ESPN floors every undrafted player at the same ADP, so hundreds of them tie at once.
    """
    ranked: list[tuple[int, int]] = []
    previous_key: float | None = None
    rank = 0
    for position, (player_id, key) in enumerate(ordered, start=1):
        if previous_key is None or key != previous_key:
            rank = position
            previous_key = key
        ranked.append((player_id, rank))
    return ranked


def _projection_specs(db: Session) -> list[SourceSpec]:
    """One source per distinct `Projection.source`, on its newest stored season.

    There is one `kind` today (`projected_season`), so a value source is exactly a source name.
    When rest-of-season projections arrive as a second kind, this is the function that grows a
    source per (source, kind) — not the adapters below, which only ever see a spec.
    """
    rows = db.execute(
        select(Projection.source, Projection.season)
        .where(Projection.kind == SEASON_PROJECTION_KIND)
        .distinct()
        .order_by(Projection.source, Projection.season.desc())
    ).all()

    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for source, season in rows:
        if source in seen:
            continue
        seen.add(source)
        specs.append(
            SourceSpec(
                id=f"{KIND_PROJECTION}:{source}",
                label=f"{source} projection",
                kind=KIND_PROJECTION,
                source=source,
                season=season,
            )
        )
    return specs


def _adp_specs(db: Session) -> list[SourceSpec]:
    """One source per distinct `AdpEntry.source`, on its newest stored season.

    ADP is redraft by nature — it is what a room did, and rooms draft for this season. It is
    offered under BOTH board horizons anyway, because on a dynasty board the market's redraft
    read is the thing you are trying to beat: the gap between it and a dynasty consensus is
    the whole edge. Its `ranking_horizon` is left null rather than set to 'redraft' so nothing
    downstream mistakes it for an imported list that declared a tag.
    """
    rows = db.execute(
        select(AdpEntry.source, AdpEntry.season)
        .distinct()
        .order_by(AdpEntry.source, AdpEntry.season.desc())
    ).all()

    specs: list[SourceSpec] = []
    seen: set[str] = set()
    for source, season in rows:
        if source in seen:
            continue
        seen.add(source)
        specs.append(
            SourceSpec(
                id=f"{KIND_ADP}:{source}",
                label=f"{source} ADP",
                kind=KIND_ADP,
                source=source,
                season=season,
            )
        )
    return specs


def _ranking_specs(db: Session, ranking_horizon: str) -> list[SourceSpec]:
    """One source per `RankingSet` TAGGED for this board horizon — and only those.

    This is where the two vocabularies meet, and the filter is the whole reason the tag is a
    stored column: a dynasty top-200 simply is not an answer to "who helps me this season", so
    under `current_year` it does not appear as a source you could accidentally average in.
    """
    sets = db.scalars(
        select(RankingSet)
        .where(RankingSet.horizon == ranking_horizon)
        .order_by(RankingSet.source, RankingSet.name, RankingSet.season.desc())
    ).all()
    return [
        SourceSpec(
            id=f"{KIND_RANKING}:{ranking_set.id}",
            label=ranking_set.name,
            kind=KIND_RANKING,
            source=ranking_set.source,
            season=ranking_set.season,
            ranking_horizon=ranking_set.horizon,
        )
        for ranking_set in sets
    ]


def available_specs(db: Session, horizon: str) -> list[SourceSpec]:
    """Every source that can rank players under this board horizon, without reading its rows.

    Projections first, then markets, then imported boards — the order the chips are shown in,
    decided here so two clients can't disagree about it.
    """
    return [
        *_projection_specs(db),
        *_adp_specs(db),
        *_ranking_specs(db, ranking_tag_for_horizon(horizon)),
    ]


def _projection_members(db: Session, spec: SourceSpec, horizon: str) -> list[tuple[int, int]]:
    """A projection source's players, best first, competition-ranked by the horizon's value.

    The ordering is NOT recomputed here: it comes straight out of `ranked_board`, which is the
    one place that knows how a horizon is priced (the age curve, the settings it comes from,
    and the name tie-break that keeps two identical requests in the same order). A second
    expression of "who does ESPN's projection rank first" is a second board.
    """
    # Imported inside the function: `app.api.players` is an HTTP module that imports the
    # valuation engine, and importing it at module scope would make `app.ranking` load through
    # the `app.api` package and depend on the order routers are mounted in.
    from app.api.players import TIERS_OFF, ranked_board

    board = ranked_board(
        db,
        source=spec.source,
        season=spec.season,
        horizon=horizon,
        # No tiers: this adapter wants the ORDER, and cutting it into tiers would be work
        # thrown away plus a second (irrelevant) opinion on the response.
        tiers=TIERS_OFF,
    )
    return _competition_ranked(
        [
            (entry.player.espn_player_id, horizon_value(entry.value, horizon))
            for entry in board.entries
        ]
    )


def _adp_members(db: Session, spec: SourceSpec) -> list[tuple[int, int]]:
    """An ADP source's players, best first — lower ADP is better, so the order inverts.

    A row with a NULL `adp` is dropped rather than sunk to the bottom: the source stored an
    entry for him but published no number, which is an absence of an opinion, not a bad one.
    Downstream that makes him missing from this source, which is the honest thing for a
    consensus to know.
    """
    rows = db.execute(
        select(AdpEntry.player_id, AdpEntry.adp, Player.full_name)
        .join(Player, Player.espn_player_id == AdpEntry.player_id)
        .where(
            AdpEntry.source == spec.source,
            AdpEntry.season == spec.season,
            AdpEntry.adp.is_not(None),
        )
        # Name breaks the tie so the ordering is stable between identical calls; the ranks
        # themselves ignore it, because tied ADPs share a competition rank.
        .order_by(AdpEntry.adp, Player.full_name)
    ).all()
    return _competition_ranked([(player_id, float(adp)) for player_id, adp, _ in rows])


def _ranking_members(db: Session, spec: SourceSpec) -> list[tuple[int, int]]:
    """A ranking set's players, in the order the source published, at the ranks it published.

    `RankingEntry.rank` is stored, not derived (see `app.db.models.ranking`): gaps in a
    source's own numbering are what that source meant, and renumbering 1..N would hand back a
    different board than the one imported.
    """
    ranking_set_id = int(spec.id.split(":", 1)[1])
    rows = db.execute(
        select(RankingEntry.player_id, RankingEntry.rank)
        .join(Player, Player.espn_player_id == RankingEntry.player_id)
        .where(RankingEntry.ranking_set_id == ranking_set_id)
        .order_by(RankingEntry.rank, Player.full_name)
    ).all()
    return [(player_id, rank) for player_id, rank in rows]


def _members(db: Session, spec: SourceSpec, horizon: str) -> list[tuple[int, int]]:
    """Dispatch to the adapter for this spec's storage shape."""
    if spec.kind == KIND_PROJECTION:
        return _projection_members(db, spec, horizon)
    if spec.kind == KIND_ADP:
        return _adp_members(db, spec)
    if spec.kind == KIND_RANKING:
        return _ranking_members(db, spec)
    raise ValueError(f"Unknown source kind {spec.kind!r}; supported: {list(KINDS)}.")


def load_catalog(db: Session, horizon: str) -> SourceCatalog:
    """Read every available source under this horizon and place it over one shared pool.

    Deliberately one pass for all of them rather than a cheap listing endpoint and a separate
    loading path: the pool is the union of what they cover, so listing a source's `player_count`
    already costs reading its rows, and two paths would be two chances to disagree about what
    a source contains. The whole thing is a few thousand rows.
    """
    specs = available_specs(db, horizon)
    members = {spec.id: _members(db, spec, horizon) for spec in specs}

    pool = frozenset(player_id for entries in members.values() for player_id, _ in entries)
    pool_size = len(pool)

    return SourceCatalog(
        horizon=horizon,
        ranking_horizon=ranking_tag_for_horizon(horizon),
        pool=pool,
        sources=tuple(
            RankingSource(
                spec=spec,
                placements=tuple(
                    Placement(
                        player_id=player_id,
                        rank=rank,
                        percentile=percentile_for(rank, pool_size),
                    )
                    for player_id, rank in members[spec.id]
                    # A no-op while the pool IS the union of every source, and the line that
                    # keeps this honest if the pool is ever narrowed to, say, the projectable
                    # players: a percentile has to be over the pool it claims to be over.
                    if player_id in pool
                ),
            )
            for spec in specs
        ),
    )
