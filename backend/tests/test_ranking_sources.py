"""app.ranking.sources / app.ranking.horizons — the three adapters and the one mapping.

The shape being asserted is always the same, because the whole point of the adapter layer is
that it is the same: given (db, horizon), a source hands back (player_id, rank, percentile)
over one shared pool, whatever it is stored as underneath.
"""

import pytest
from sqlalchemy import select

from app.db.models import AdpEntry, Player, Projection
from app.db.models.ranking import HORIZON_DYNASTY as TAG_DYNASTY
from app.db.models.ranking import HORIZON_REDRAFT as TAG_REDRAFT
from app.ranking import (
    KIND_ADP,
    KIND_PROJECTION,
    KIND_RANKING,
    RANKING_TAG_BY_HORIZON,
    UnknownHorizon,
    available_specs,
    load_catalog,
    percentile_for,
    ranking_tag_for_horizon,
)
from app.valuation import HORIZON_CURRENT_YEAR, HORIZON_DYNASTY

# --- the mapping between the two horizon vocabularies ---------------------------------------


def test_the_board_horizon_maps_to_the_ranking_tag_it_asks_the_same_question_as():
    """dynasty -> 'dynasty', current_year -> 'redraft'. The one translation, in one place."""
    assert ranking_tag_for_horizon(HORIZON_DYNASTY) == TAG_DYNASTY
    assert ranking_tag_for_horizon(HORIZON_CURRENT_YEAR) == TAG_REDRAFT


def test_the_mapping_covers_both_board_horizons_and_nothing_else():
    assert set(RANKING_TAG_BY_HORIZON) == {HORIZON_DYNASTY, HORIZON_CURRENT_YEAR}
    assert set(RANKING_TAG_BY_HORIZON.values()) == {TAG_DYNASTY, TAG_REDRAFT}


def test_an_unknown_horizon_raises_rather_than_falling_back_to_dynasty():
    """A typo'd horizon must not quietly build a board from lists answering another question."""
    with pytest.raises(UnknownHorizon) as caught:
        ranking_tag_for_horizon("redraft")  # the RANK-SET vocabulary, not the board's

    assert "current_year" in str(caught.value) and "dynasty" in str(caught.value)


# --- the percentile scale --------------------------------------------------------------------


def test_percentile_places_a_rank_in_the_shared_pool():
    assert percentile_for(1, 101) == 100.0
    assert percentile_for(51, 101) == 50.0
    assert percentile_for(101, 101) == 0.0


def test_a_rank_off_the_bottom_of_the_pool_is_clamped_to_zero_not_negative():
    """A published rank can point past the pool — a source numbering with gaps runs over."""
    assert percentile_for(400, 101) == 0.0


def test_a_pool_of_one_is_a_hundred_rather_than_a_division_by_zero():
    assert percentile_for(1, 1) == 100.0


# --- what is available, per horizon ----------------------------------------------------------


def test_both_horizons_offer_the_value_and_market_sources(db, synced):
    for horizon in (HORIZON_DYNASTY, HORIZON_CURRENT_YEAR):
        kinds = {spec.kind for spec in available_specs(db, horizon)}
        assert kinds == {KIND_PROJECTION, KIND_ADP}, horizon


def test_a_ranking_set_is_a_source_only_under_the_horizon_its_tag_maps_to(
    db, synced, make_ranking_set
):
    """The eligibility rule the whole two-vocabulary split exists for."""
    top = list(db.scalars(select(Projection.player_id).limit(3)))
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top[0]: 1, top[1]: 2})
    make_ranking_set("Rest of Season", TAG_REDRAFT, {top[1]: 1, top[2]: 2})

    dynasty = {spec.label for spec in available_specs(db, HORIZON_DYNASTY)}
    win_now = {spec.label for spec in available_specs(db, HORIZON_CURRENT_YEAR)}

    assert "Dynasty Top 200" in dynasty and "Dynasty Top 200" not in win_now
    assert "Rest of Season" in win_now and "Rest of Season" not in dynasty


def test_source_ids_are_stable_handles_a_client_can_select_with(db, synced, make_ranking_set):
    top = list(db.scalars(select(Projection.player_id).limit(2)))
    ranking_set = make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {top[0]: 1, top[1]: 2})

    ids = {spec.id for spec in available_specs(db, HORIZON_DYNASTY)}

    # Deliberately NOT season-keyed: next season's sync must not invalidate a saved selection.
    assert ids == {"projection:espn", "adp:espn", f"ranking:{ranking_set.id}"}


def test_a_database_with_nothing_in_it_offers_no_sources_rather_than_failing(db):
    assert available_specs(db, HORIZON_DYNASTY) == []


# --- the projection adapter -------------------------------------------------------------------


def test_a_projection_source_ranks_by_points_per_game_under_the_win_now_horizon(db, synced):
    catalog = load_catalog(db, HORIZON_CURRENT_YEAR)
    source = catalog.get("projection:espn")

    expected = list(
        db.scalars(select(Projection.player_id).order_by(Projection.fantasy_points_per_game.desc()))
    )

    assert [placement.player_id for placement in source.placements] == expected
    assert [placement.rank for placement in source.placements[:3]] == [1, 2, 3]
    assert source.spec.kind == KIND_PROJECTION
    # No declared tag: a value source derives both horizons from production instead.
    assert source.spec.ranking_horizon is None


def test_the_same_projection_source_orders_differently_under_the_two_horizons(db, aged):
    """The age curve is the difference, and it has to actually move the board."""
    win_now = load_catalog(db, HORIZON_CURRENT_YEAR).get("projection:espn")
    dynasty = load_catalog(db, HORIZON_DYNASTY).get("projection:espn")

    assert {p.player_id for p in win_now.placements} == {p.player_id for p in dynasty.placements}
    assert [p.player_id for p in win_now.placements] != [p.player_id for p in dynasty.placements]

    # And in the direction the curve says: someone old slides down, someone young climbs.
    win_now_rank = {p.player_id: p.rank for p in win_now.placements}
    dynasty_rank = {p.player_id: p.rank for p in dynasty.placements}
    ages = dict(db.execute(select(Player.espn_player_id, Player.age)).all())

    oldest = max((pid for pid in win_now_rank if ages.get(pid)), key=lambda pid: ages[pid])
    assert dynasty_rank[oldest] > win_now_rank[oldest]


def test_tied_values_share_a_rank_rather_than_being_split_by_the_tie_break(db, synced):
    """Equal opinions are equal: a tie-break must not invent a pecking order."""
    rows = list(db.scalars(select(Projection).order_by(Projection.fantasy_points_per_game)))
    rows[0].fantasy_points_per_game = rows[1].fantasy_points_per_game = 4.0
    db.commit()

    source = load_catalog(db, HORIZON_CURRENT_YEAR).get("projection:espn")
    ranks = {placement.player_id: placement.rank for placement in source.placements}

    assert ranks[rows[0].player_id] == ranks[rows[1].player_id]
    # Competition ranking: the tie eats the place below it (…, n, n, n+2).
    assert sorted(ranks.values())[-2:] == [len(ranks) - 1, len(ranks) - 1]


# --- the ADP adapter ---------------------------------------------------------------------------


def test_an_adp_source_inverts_lower_is_better(db, synced):
    source = load_catalog(db, HORIZON_DYNASTY).get("adp:espn")

    adps = dict(db.execute(select(AdpEntry.player_id, AdpEntry.adp)).all())
    ordered = [adps[placement.player_id] for placement in source.placements]

    assert ordered == sorted(ordered)
    assert source.spec.kind == KIND_ADP
    assert source.placements[0].rank == 1
    assert source.placements[0].percentile == 100.0


def test_the_market_is_a_source_under_both_horizons(db, synced):
    """Redraft by nature, and on a dynasty board it is the thing you're trying to beat."""
    for horizon in (HORIZON_DYNASTY, HORIZON_CURRENT_YEAR):
        assert load_catalog(db, horizon).get("adp:espn") is not None


def test_a_player_with_no_published_adp_is_missing_from_the_source_not_last_on_it(db, synced):
    """The source stored a row and published no number: an absence of opinion, not a bad one."""
    entry = db.scalars(select(AdpEntry)).first()
    entry.adp = None
    db.commit()

    source = load_catalog(db, HORIZON_DYNASTY).get("adp:espn")

    assert entry.player_id not in {placement.player_id for placement in source.placements}


def test_players_tied_at_the_same_adp_share_a_rank_and_a_percentile(db, synced):
    """ESPN floors every undrafted player at one ADP; several hundred tie at once."""
    entries = list(db.scalars(select(AdpEntry).order_by(AdpEntry.adp.desc()).limit(3)))
    for entry in entries:
        entry.adp = 140.0
    db.commit()

    source = load_catalog(db, HORIZON_DYNASTY).get("adp:espn")
    placed = {p.player_id: p for p in source.placements}
    tied = [placed[entry.player_id] for entry in entries]

    assert len({placement.rank for placement in tied}) == 1
    assert len({placement.percentile for placement in tied}) == 1


# --- the ranking adapter -------------------------------------------------------------------------


def test_a_ranking_source_reports_the_rank_the_source_published_gaps_and_all(
    db, synced, make_ranking_set
):
    """Rank is stored, not derived — renumbering 1..N would disagree with the imported board."""
    ids = list(db.scalars(select(Projection.player_id).limit(4)))
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, dict(zip(ids, [1, 2, 5, 9], strict=True)))

    catalog = load_catalog(db, HORIZON_DYNASTY)
    source = next(source for source in catalog.sources if source.spec.kind == KIND_RANKING)

    assert [placement.rank for placement in source.placements] == [1, 2, 5, 9]
    assert [placement.player_id for placement in source.placements] == ids
    assert source.spec.ranking_horizon == TAG_DYNASTY


def test_a_ranking_source_only_loads_under_its_mapped_horizon(db, synced, make_ranking_set):
    ids = list(db.scalars(select(Projection.player_id).limit(2)))
    ranking_set = make_ranking_set(
        "Dynasty Top 200", TAG_DYNASTY, dict(zip(ids, [1, 2], strict=True))
    )

    assert load_catalog(db, HORIZON_DYNASTY).get(f"ranking:{ranking_set.id}") is not None
    assert load_catalog(db, HORIZON_CURRENT_YEAR).get(f"ranking:{ranking_set.id}") is None


# --- the shared pool ------------------------------------------------------------------------------


def test_the_pool_is_the_union_of_every_available_source(db, synced, make_ranking_set):
    projected = set(db.scalars(select(Projection.player_id)))
    priced = set(db.scalars(select(AdpEntry.player_id)))
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, {next(iter(projected)): 1})

    catalog = load_catalog(db, HORIZON_DYNASTY)

    assert catalog.pool == projected | priced
    assert catalog.pool_size == len(projected | priced)


def test_every_source_is_scored_against_that_one_denominator(db, synced, make_ranking_set):
    """A four-name list and a sixty-name market read off the same 0-100 scale."""
    ids = list(db.scalars(select(Projection.player_id).limit(4)))
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, dict(zip(ids, [1, 2, 3, 4], strict=True)))

    catalog = load_catalog(db, HORIZON_DYNASTY)
    size = catalog.pool_size

    for source in catalog.sources:
        for placement in source.placements:
            assert placement.percentile == percentile_for(placement.rank, size)

    # Which is the point: rank 2 means the same thing on the four-name list as on the
    # sixty-name one, so a spread between them is a real disagreement rather than an artefact
    # of how far down each source happened to keep going.
    short = next(source for source in catalog.sources if source.spec.kind == KIND_RANKING)
    long = catalog.get("adp:espn")
    assert short.player_count < long.player_count
    assert next(p.percentile for p in short.placements if p.rank == 2) == next(
        p.percentile for p in long.placements if p.rank == 2
    )


def test_the_pool_does_not_move_when_a_client_picks_fewer_sources(db, synced, make_ranking_set):
    """Ticking a box must never restate the other sources' percentiles."""
    ids = list(db.scalars(select(Projection.player_id).limit(2)))
    make_ranking_set("Dynasty Top 200", TAG_DYNASTY, dict(zip(ids, [1, 2], strict=True)))

    catalog = load_catalog(db, HORIZON_DYNASTY)
    selected, unknown = catalog.select(["projection:espn"])

    assert unknown == []
    assert selected[0].placements[0].percentile == percentile_for(1, catalog.pool_size)


def test_an_unknown_source_id_comes_back_named_rather_than_silently_dropped(db, synced):
    catalog = load_catalog(db, HORIZON_DYNASTY)

    selected, unknown = catalog.select(["projection:espn", "ranking:999"])

    assert [source.id for source in selected] == ["projection:espn"]
    assert unknown == ["ranking:999"]
