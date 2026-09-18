"""Average several sources into one board, and say how much they disagreed.

Pure: in go `RankingSource`s from `app.ranking.sources`, out comes an ordered list of rows.
Nothing here touches the database, the settings, or a player's name — which is what lets the
rules below be asserted on a five-row fixture instead of on a synced league.

EQUAL WEIGHT, deliberately. Per-source weighting ("trust Dizzle 2x ESPN's projection") is a
later task and a genuinely different feature: it needs a place to store the weights and a way
to calibrate them. Until then every selected source counts once, which is at least an opinion
we can explain.

**A player missing from a source is excluded from that source's average, never imputed.** The
alternative — treating "not on this list" as "last on this list" — would be a fabrication: a
449-name dynasty board that stops at 449 is not saying the 450th player is worthless, it is
saying nothing about him at all, and a consensus that turned that silence into a bottom rank
would bury exactly the deep-sleeper rows the board exists to surface. The cost of the honest
rule is the mirror-image trap, so the rows carry what is needed to see it: a player one source
loves and the other two have never heard of comes back with that source's rank as his
consensus, `sources_present = 1`, and every source that skipped him named in `sources_missing`.
Read the coverage before you read the consensus.

A row appears if at least one selected source ranks him. Zero-coverage players are not rows.

DISAGREEMENT is the spread across the sources that DO rank him, and it is reported in two
units because the board can be read in two: `spread` in percentile points (the scale that
means the same thing on a 449-name list and a 1,095-name one — this is the one to colour by)
and `rank_spread` in places. Both are null when fewer than two sources rank him: one source
cannot disagree with itself, and reporting 0 there would paint perfect agreement.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.ranking.sources import Placement, RankingSource

# How the selected sources are averaged. Both read off the same placements; see the note in
# `app.ranking.sources` on why percentile is the readable scale rather than a re-ordering one.
METHOD_RANK = "rank"
METHOD_PERCENTILE = "percentile"
METHODS = (METHOD_RANK, METHOD_PERCENTILE)


class UnknownMethod(ValueError):
    """A consensus method that is not one of METHODS. Routes turn this into a 400."""


@dataclass(frozen=True)
class ConsensusRow:
    """One player's line on the consensus board."""

    player_id: int
    # The equal-weight average of the selected sources that rank him, in the method's units:
    # places for 'rank' (lower is better), percentile points for 'percentile' (higher is).
    consensus: float
    # source id -> where that source put him. A source that doesn't rank him has NO entry
    # here, rather than an entry full of nulls: the absence is the fact.
    cells: dict[str, Placement]
    # Selected source ids that rank him, and those that don't, both in selection order.
    sources_present: tuple[str, ...]
    sources_missing: tuple[str, ...]
    # max-min across the sources that rank him. Null below two of them.
    spread: float | None
    rank_spread: float | None


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def consensus_board(
    sources: Sequence[RankingSource],
    method: str = METHOD_RANK,
    *,
    names: Mapping[int, str] | None = None,
) -> list[ConsensusRow]:
    """Average the selected sources into one ordered board.

    `names` is only a tie-break: two players with an identical consensus are ordered by name
    so the board doesn't shuffle between two identical requests. Passing it is optional
    because it is the one thing here that is about presentation, and the engine stays
    assertable without a database.
    """
    if method not in METHODS:
        raise UnknownMethod(
            f"Unknown consensus method {method!r}; supported: "
            + ", ".join(repr(name) for name in METHODS)
            + "."
        )
    if not sources:
        return []

    # Deduplicated, order preserved: equal weight means a source counts ONCE however many
    # times it was asked for. The average already behaves (the placements are a dict keyed by
    # source id), so without this only the coverage counters would double-count it — which is
    # the half a client reads to decide how much to trust the number.
    selected = tuple(dict.fromkeys(source.id for source in sources))
    placements = {source.id: source.by_player() for source in sources}

    rows: list[ConsensusRow] = []
    for player_id in {player_id for lookup in placements.values() for player_id in lookup}:
        cells = {
            source_id: lookup[player_id]
            for source_id, lookup in placements.items()
            if player_id in lookup
        }
        present = tuple(source_id for source_id in selected if source_id in cells)
        missing = tuple(source_id for source_id in selected if source_id not in cells)

        ranks = [cell.rank for cell in cells.values()]
        percentiles = [cell.percentile for cell in cells.values()]
        consensus = _mean(ranks) if method == METHOD_RANK else _mean(percentiles)

        rows.append(
            ConsensusRow(
                player_id=player_id,
                consensus=consensus,
                cells=cells,
                sources_present=present,
                sources_missing=missing,
                spread=(max(percentiles) - min(percentiles)) if len(cells) > 1 else None,
                rank_spread=float(max(ranks) - min(ranks)) if len(cells) > 1 else None,
            )
        )

    # Lower is better for ranks, higher for percentiles — one sort, the direction flipped,
    # rather than two orderings that could drift apart. Coverage breaks a tie before the name
    # does: between two players on the same average, the one more sources actually looked at
    # is the more supported opinion.
    descending = method == METHOD_PERCENTILE
    rows.sort(
        key=lambda row: (
            -row.consensus if descending else row.consensus,
            -len(row.sources_present),
            names.get(row.player_id, "") if names else "",
            row.player_id,
        )
    )
    return rows
