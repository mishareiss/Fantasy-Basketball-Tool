"""The `ranking` import kind: the set is the unit, and re-importing it replaces it.

`test_ingest_pipeline` already covers parsing, matching, review and dry-run behaviour, and
nothing here re-tests them. What is specific to this kind:

* rank comes from the column when the file has one, and from the file's order when it doesn't
  — and a row we couldn't place leaves a *gap*, rather than promoting everyone below it;
* tier is a label (text) and value is a number, both optional;
* a set is identified by (source, name, season), and importing it again REPLACES its entries:
  the players who fell off are gone, the rest are re-ranked, and nothing is duplicated.
"""

import pytest
from sqlalchemy import func, select

from app.db.models import PlayerAlias, RankingEntry, RankingSet
from app.ingest import (
    PLANNED_KINDS,
    RANKING_COLUMNS,
    ImportParseError,
    accept_only_certain,
    kind_names,
    run_import,
)
from app.ingest.parser import detect_columns
from tests.conftest import SEASON

SOURCE = "hashtag"
SET_NAME = "Dynasty Top 200"

SGA_ID = 4278073
JOKIC_ID = 3112335

# Who the with-rank fixture resolves to, and at which rank. The ranks are the source's own —
# note the jumps (5 -> 8 -> 12): a ranking means the numbers it printed.
EXPECTED_RANKS = {
    "Shai Gilgeous-Alexander": 1,
    "Nikola Jokic": 2,
    "Giannis Antetokounmpo": 3,
    "Luka Doncic": 4,
    "Victor Wembanyama": 5,
    "Cooper Flagg": 8,
    "Karl-Anthony Towns": 12,
    "Alperen Sengun": 14,
    "De'Aaron Fox": 19,
    "Michael Porter Jr.": 31,
    "D.J. Carton": 55,
}

# The order-only fixture, by file position. 4 and 7 are missing on purpose: those rows are
# players we carry nobody for, and their places stay empty.
EXPECTED_ORDER_RANKS = {
    "Shai Gilgeous-Alexander": 1,
    "Nikola Jokic": 2,
    "Luka Doncic": 3,
    "Cooper Flagg": 5,
    "Victor Wembanyama": 6,
    "De'Aaron Fox": 8,
    "Michael Porter Jr.": 9,
}


def _import(db, csv, **kwargs):
    kwargs.setdefault("season", SEASON)
    kwargs.setdefault("options", {"name": SET_NAME})
    kwargs.setdefault("dry_run", False)
    return run_import(db, kind="ranking", source=SOURCE, text=csv, **kwargs)


def _sets(db) -> list[RankingSet]:
    return list(db.scalars(select(RankingSet).order_by(RankingSet.id)))


def _all_entries(db) -> list[RankingEntry]:
    return list(db.scalars(select(RankingEntry).order_by(RankingEntry.rank)))


def _entries(db, ranking_set: RankingSet) -> dict[str, int]:
    """`player name -> rank` for one set, which is what the set actually asserts."""
    rows = db.execute(
        select(RankingEntry).where(RankingEntry.ranking_set_id == ranking_set.id)
    ).scalars()
    return {row.player.full_name: row.rank for row in rows}


def test_ranking_is_registered_and_market_line_is_the_last_stub():
    assert kind_names() == ["adp", "projection", "ranking"]
    assert sorted(PLANNED_KINDS) == ["market_line"]


# --- columns ------------------------------------------------------------------------------


def test_the_rank_tier_and_value_columns_are_found_by_alias():
    columns = detect_columns(["Player", "Team", "Pos", "Rank", "Tier", "Score"], RANKING_COLUMNS)

    assert columns.as_dict() == {
        "name": "Player",
        "team": "Team",
        "positions": "Pos",
        "rank": "Rank",
        "tier": "Tier",
        "value": "Score",
    }


@pytest.mark.parametrize(
    ("header", "field_name"),
    [
        ("RK", "rank"),
        ("#", "rank"),
        ("Overall", "rank"),
        ("OVR", "rank"),
        ("Pos Rank", "rank"),
        ("PosRk", "rank"),
        ("TR", "tier"),
        ("Group", "tier"),
        ("Grp", "tier"),
        ("Value", "value"),
        ("Rating", "value"),
        ("Proj", "value"),
    ],
)
def test_each_alias_lands_on_the_field_it_names(header, field_name):
    columns = detect_columns(["Player", header], RANKING_COLUMNS)

    assert columns.as_dict()[field_name] == header


def test_no_rank_column_is_fine_because_the_order_is_the_ranking():
    """The one required-column rule this kind deliberately doesn't have."""
    columns = detect_columns(["Player", "Team"], RANKING_COLUMNS)

    assert "rank" not in columns.as_dict()


def test_a_plain_pos_column_stays_the_positions_column(ranking_order_csv):
    """ "Pos" is the position column in every export there is. If `rank` were allowed to claim
    it, the preview would report `rank: 'Pos'` for a file whose ranks came from its order —
    a wrong column map, which is the one mis-detection a row-by-row list can't reveal."""
    columns = detect_columns(["Player", "Team", "Pos"], RANKING_COLUMNS)

    assert columns.as_dict() == {"name": "Player", "team": "Team", "positions": "Pos"}


# --- rank resolution ----------------------------------------------------------------------


def test_rank_comes_off_the_column_gaps_and_all(players, ranking_csv):
    summary = _import(players, ranking_csv)

    assert summary.matched == 11
    assert _entries(players, _sets(players)[0]) == EXPECTED_RANKS
    assert "rank from column for 11 row(s)" in summary.notes[1]


def test_with_no_rank_column_the_file_order_is_the_rank(players, ranking_order_csv):
    summary = _import(players, ranking_order_csv)

    assert _entries(players, _sets(players)[0]) == EXPECTED_ORDER_RANKS
    assert "from file order for 7" in summary.notes[1]


def test_a_row_we_could_not_place_leaves_a_gap_it_does_not_renumber(players, ranking_order_csv):
    """Positions 4 and 7 are unmatched names; #5 must still be #5."""
    _import(players, ranking_order_csv)

    ranks = _entries(players, _sets(players)[0])

    assert sorted(ranks.values()) == [1, 2, 3, 5, 6, 8, 9]
    assert ranks["Cooper Flagg"] == 5  # not 4, which is what counting accepted rows would give


def test_a_row_held_for_review_leaves_a_gap_too(players, ranking_order_csv):
    """`--strict` holds the fuzzy name at position 6; the rows below it do not move up."""
    _import(players, ranking_order_csv, accept=accept_only_certain)

    ranks = _entries(players, _sets(players)[0])

    assert "Victor Wembanyama" not in ranks
    assert sorted(ranks.values()) == [1, 2, 3, 5, 8, 9]


# --- tiers and values ---------------------------------------------------------------------


def test_tiers_are_labels_not_numbers(players, ranking_csv):
    _import(players, ranking_csv)

    tiers = {entry.player.full_name: entry.tier for entry in _all_entries(players)}

    assert tiers["Shai Gilgeous-Alexander"] == "1"  # the string "1", not 1.0
    assert tiers["Luka Doncic"] == "Tier 2"
    assert tiers["De'Aaron Fox"] == "Elite"  # a source that names its tiers keeps its names


def test_an_empty_or_nullish_tier_is_absent_rather_than_a_literal_dash(players, ranking_csv):
    _import(players, ranking_csv)

    tiers = {entry.player.full_name: entry.tier for entry in _all_entries(players)}

    assert tiers["D.J. Carton"] is None  # the cell says "--"


def test_the_score_the_source_printed_is_stored_and_an_empty_one_is_null(players, ranking_csv):
    _import(players, ranking_csv)

    values = {entry.player.full_name: entry.value for entry in _all_entries(players)}

    assert values["Shai Gilgeous-Alexander"] == 98.4
    assert values["Michael Porter Jr."] is None


def test_a_file_with_neither_tier_nor_value_imports_anyway(players, ranking_order_csv):
    _import(players, ranking_order_csv)

    assert {entry.value for entry in _all_entries(players)} == {None}


# --- set identity and wholesale replace ---------------------------------------------------


def test_the_set_is_named_by_the_option_and_keyed_with_source_and_season(players, ranking_csv):
    _import(players, ranking_csv)

    (stored,) = _sets(players)
    assert (stored.source, stored.name, stored.season) == (SOURCE, SET_NAME, SEASON)


def test_without_a_name_the_set_is_the_source(players, ranking_csv):
    _import(players, ranking_csv, options=None)

    (stored,) = _sets(players)
    assert stored.name == SOURCE


def test_a_different_name_is_a_different_set_not_a_replacement(players, ranking_csv):
    _import(players, ranking_csv)
    _import(players, ranking_csv, options={"name": "Redraft Top 200"})

    assert [stored.name for stored in _sets(players)] == [SET_NAME, "Redraft Top 200"]
    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 22


def test_the_same_name_replaces_the_set_wholesale(players, ranking_csv):
    """The point of the kind. Version two drops players and re-orders the rest."""
    _import(players, ranking_csv)
    revised = "\n".join(
        line
        for line in ranking_csv.splitlines()
        # Jokić falls off the board entirely, and so does Towns.
        if "Jokić" not in line and "Towns" not in line
    ).replace('1,"Gilgeous-Alexander, Shai"', '3,"Gilgeous-Alexander, Shai"')

    summary = _import(players, revised)

    (stored,) = _sets(players)  # still one set, not two
    ranks = _entries(players, stored)
    assert "Nikola Jokic" not in ranks and "Karl-Anthony Towns" not in ranks
    assert ranks["Shai Gilgeous-Alexander"] == 3  # re-ranked, not left at 1
    assert len(ranks) == 9
    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 9
    assert "replacing 11 entries with 9, 2 player(s) drop off" in summary.notes[0]


def test_re_importing_the_same_file_yields_exactly_that_set_once(players, ranking_csv):
    _import(players, ranking_csv)
    second = _import(players, ranking_csv)

    assert players.scalar(select(func.count()).select_from(RankingSet)) == 1
    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 11
    # No duplicate (set, player) rows, and the counters say "nothing moved".
    assert (second.rows_created, second.rows_updated) == (0, 0)
    assert second.rows_unchanged == 11


def test_a_moved_player_reads_as_an_update_not_a_new_entry(players, ranking_csv):
    _import(players, ranking_csv)

    summary = _import(players, ranking_csv.replace("19,DeAaron Fox", "9,DeAaron Fox"))

    assert (summary.rows_created, summary.rows_updated, summary.rows_unchanged) == (0, 1, 10)
    assert _entries(players, _sets(players)[0])["De'Aaron Fox"] == 9


def test_an_import_that_resolved_nothing_leaves_the_set_alone(players, ranking_csv):
    """Refusing to empty a good board over a file whose names we couldn't place."""
    _import(players, ranking_csv)

    summary = _import(players, "Rank,Player\n1,Someone Nobody Carries\n")

    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 11
    assert "left untouched" in summary.notes[0]


# --- options, dry runs, aliases -----------------------------------------------------------


def test_an_unknown_option_is_refused_rather_than_ignored(players, ranking_csv):
    """A dropped `--name` would replace the wrong list, and the entries it ate are gone."""
    with pytest.raises(ImportParseError, match="unknown option"):
        _import(players, ranking_csv, options={"basis": "season", "name": SET_NAME})

    assert players.scalar(select(func.count()).select_from(RankingSet)) == 0


def test_a_dry_run_writes_nothing_but_counts_for_real(players, ranking_csv):
    summary = _import(players, ranking_csv, dry_run=True)

    assert summary.rows_created == 11
    assert "new set of 11 entries" in summary.notes[0]
    assert players.scalar(select(func.count()).select_from(RankingSet)) == 0
    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 0
    assert players.scalar(select(func.count()).select_from(PlayerAlias)) == 0


def test_a_dry_run_previews_the_replacement_before_it_happens(players, ranking_csv):
    _import(players, ranking_csv)

    summary = _import(players, ranking_csv, dry_run=True)

    assert "replacing 11 entries with 11" in summary.notes[0]
    assert summary.rows_unchanged == 11
    assert players.scalar(select(func.count()).select_from(RankingEntry)) == 11


def test_recording_the_aliases_is_idempotent(players, ranking_csv):
    first = _import(players, ranking_csv)
    second = _import(players, ranking_csv)

    assert (first.aliases_created, first.aliases_existing) == (11, 0)
    assert (second.aliases_created, second.aliases_existing) == (0, 11)
    assert players.scalar(select(func.count()).select_from(PlayerAlias)) == 11
