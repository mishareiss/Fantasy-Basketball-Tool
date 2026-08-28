"""The `projection` import kind: right columns, right arithmetic, our scoring, once.

The pipeline itself is covered by `test_ingest_pipeline`; nothing here re-tests matching or
dry-run behaviour. What is specific to this kind, and what these tests are about:

* it finds stat columns through whatever a source calls them, and ignores everything a
  coefficient can't multiply;
* per-game x GP is the season total, and `--basis season` goes the other way;
* the fantasy points stored are the ones `score_projection` returns — not a second
  implementation that happens to agree today.
"""

import pytest
from sqlalchemy import func, select

from app.db.models import Projection
from app.ingest import (
    PLANNED_KINDS,
    PROJECTION_KIND,
    STATUS_INVALID,
    STATUS_MATCHED,
    STATUS_REVIEW,
    STATUS_UNMATCHED,
    ImportParseError,
    accept_only_certain,
    kind_names,
    run_import,
)
from app.ingest.parser import detect_columns
from app.ingest.projection import PROJECTION_COLUMNS, stat_lines
from app.scoring import ScoringRulesNotLoaded, load_scoring_engine_for_season, score_projection
from tests.conftest import SEASON

SOURCE = "hashtag"

# Who the fixture is about. Same cast as the ADP fixture, so a matching change fails in one
# obvious place rather than two subtle ones.
EXPECTED_MATCHED = {
    "Gilgeous-Alexander, Shai": "Shai Gilgeous-Alexander",
    "Nikola Jokić": "Nikola Jokic",
    "Giannis  Antetokounmpo": "Giannis Antetokounmpo",
    "Luka Dončić": "Luka Doncic",
    "Victor Wembanyma": "Victor Wembanyama",
    "Cooper Flagg": "Cooper Flagg",
    "Towns, Karl-Anthony": "Karl-Anthony Towns",
    "Alperen Şengün": "Alperen Sengun",
    "DeAaron Fox": "De'Aaron Fox",
    "Michael Porter": "Michael Porter Jr.",
    "Stephen Curry": "Stephen Curry",
    "DJ Carton": "D.J. Carton",
}

JOKIC_ID = 3112335
SGA_ID = 4278073
CURRY_ID = 3975

# Jokić's line in the fixture, per game, and what our coefficients make of it. Written out
# rather than computed, so this test disagrees with the code when the code is wrong:
#   PTS 28.4x3 + REB 12.9x4 + AST 10.4x4 + STL 1.6x7 + BLK 0.7x7
#   + 3PM 1.9x0.5 + TO 3.4x-2 + FTM 5.0x1 + FTMI (6.2-5.0)x-0.5
# MIN, FGM, FGA and FTA are unscored in our league; FG%/FT%/Rank never make it this far.
JOKIC_POINTS_PER_GAME = 193.05


def _import(db, csv, **kwargs):
    kwargs.setdefault("season", SEASON)
    return run_import(db, kind="projection", source=SOURCE, text=csv, **kwargs)


def _stored(db, player_id: int) -> Projection:
    return db.scalar(
        select(Projection).where(
            Projection.player_id == player_id,
            Projection.source == SOURCE,
            Projection.kind == PROJECTION_KIND,
            Projection.season == SEASON,
        )
    )


def test_the_registry_carries_projection_and_one_still_planned():
    assert kind_names() == ["adp", "projection", "ranking"]
    assert sorted(PLANNED_KINDS) == ["market_line"]


# --- columns ------------------------------------------------------------------------------


def test_stat_columns_are_found_through_whatever_the_source_calls_them():
    headers = ["Player", "Tm", "Pos", "GP", "MPG", "PTS", "TREB", "AST", "3PTM", "TOV", "FTM"]

    columns = detect_columns(headers, PROJECTION_COLUMNS)

    assert columns.as_dict() == {
        "name": "Player",
        "team": "Tm",
        "positions": "Pos",
        "GP": "GP",
        "MIN": "MPG",
        "PTS": "PTS",
        "REB": "TREB",
        "AST": "AST",
        "3PM": "3PTM",
        "TO": "TOV",
        "FTM": "FTM",
    }


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("3PTM", "3PM"),
        ("TPM", "3PM"),
        ("3s", "3PM"),
        ("FG3M", "3PM"),
        ("TOV", "TO"),
        ("Turnovers", "TO"),
        ("TREB", "REB"),
        ("TRB", "REB"),
        ("Total Rebounds", "REB"),
        ("Off Reb", "OREB"),
        ("DREB", "DREB"),
        ("MPG", "MIN"),
        ("Games Played", "GP"),
        ("Gms", "GP"),
        ("FT Made", "FTM"),
        ("FGA/G", "FGA"),
    ],
)
def test_header_variants_land_on_the_right_stat(header, expected):
    columns = detect_columns(["Player", "PTS", header], PROJECTION_COLUMNS)

    assert columns.as_dict()[expected] == header


def test_a_per_game_export_that_suffixes_every_header_still_reads_as_points():
    """ "PTS/G" is points; nothing here should read the trailing "G" as a games column."""
    columns = detect_columns(["Player", "PTS/G", "REB/G", "AST/G"], PROJECTION_COLUMNS)

    assert columns.as_dict() == {
        "name": "Player",
        "PTS": "PTS/G",
        "REB": "REB/G",
        "AST": "AST/G",
    }


@pytest.mark.parametrize(
    "header", ["FG%", "FT%", "3P%", "TS%", "Rank", "Tier", "ADP", "Value", "$", "Notes"]
)
def test_columns_no_coefficient_can_multiply_are_ignored(header):
    """A percentage is not a count. Scoring one would be quietly wrong, so it never arrives."""
    columns = detect_columns(["Player", "PTS", header], PROJECTION_COLUMNS)

    assert list(columns.values) == ["PTS"]


def test_a_table_with_no_stat_columns_at_all_is_refused_with_what_we_looked_for():
    with pytest.raises(ImportParseError, match="PTS"):
        detect_columns(["Player", "Team", "Rank"], PROJECTION_COLUMNS)


# --- the arithmetic -----------------------------------------------------------------------


def test_per_game_times_games_is_the_season_total():
    lines = stat_lines({"PTS": 28.4, "REB": 12.9, "GP": 73.0}, basis="per_game")

    assert lines.projected_games == 73.0
    assert lines.per_game_stats == {"PTS": 28.4, "REB": 12.9, "GP": 73.0}
    assert lines.season_totals == {"PTS": 2073.2, "REB": 941.7, "GP": 73.0}


def test_season_basis_divides_instead_of_multiplying():
    lines = stat_lines({"PTS": 2073.2, "REB": 941.7, "GP": 73.0}, basis="season")

    assert lines.season_totals == {"PTS": 2073.2, "REB": 941.7, "GP": 73.0}
    assert lines.per_game_stats == {"PTS": 28.4, "REB": 12.9, "GP": 73.0}


def test_rebounds_and_misses_a_source_implies_are_filled_in_exactly():
    """A source that prints only OREB/DREB still gets paid for rebounds — REB is worth 4."""
    lines = stat_lines(
        {"OREB": 3.1, "DREB": 8.4, "FGA": 19.0, "FGM": 11.0, "FTA": 6.2, "FTM": 5.0, "GP": 70.0},
        basis="per_game",
    )

    assert lines.per_game_stats["REB"] == 11.5
    assert lines.per_game_stats["FGMI"] == 8.0
    assert lines.per_game_stats["FTMI"] == 1.2
    # 3PMI needs 3PA as well as 3PM; neither is here, so nothing is invented.
    assert "3PMI" not in lines.per_game_stats


def test_a_printed_total_is_never_overwritten_by_a_derived_one():
    lines = stat_lines({"OREB": 3.0, "DREB": 8.0, "REB": 10.5, "GP": 70.0}, basis="per_game")

    assert lines.per_game_stats["REB"] == 10.5


def test_no_games_count_leaves_the_other_line_empty_rather_than_guessing_eighty_two():
    lines = stat_lines({"PTS": 25.6, "GP": None}, basis="per_game")

    assert lines.projected_games is None
    assert lines.per_game_stats == {"PTS": 25.6}
    assert lines.season_totals == {}


def test_zero_games_reads_as_unknown_not_as_zero():
    """Same rule the ESPN parser applies: 0 games is not a divisor and not a multiplier."""
    assert stat_lines({"PTS": 25.6, "GP": 0.0}, basis="per_game").projected_games is None


# --- the scoring --------------------------------------------------------------------------


def test_the_stored_points_are_exactly_what_score_projection_returns(priced, projection_csv):
    """The single-source-of-truth check: no second scoring path, however plausible.

    If this ever fails, an imported projection and ESPN's are no longer comparable, which is
    the entire reason the board takes a `source` parameter.
    """
    _import(priced, projection_csv, dry_run=False)
    engine = load_scoring_engine_for_season(priced, SEASON)

    stored = _stored(priced, JOKIC_ID)
    expected = score_projection(
        engine,
        stored.raw_stats,
        per_game_stats=stored.per_game_stats,
        projected_games=stored.projected_games,
    )

    assert stored.fantasy_points_total == expected.fantasy_points_total
    assert stored.fantasy_points_per_game == expected.fantasy_points_per_game
    assert stored.per_game_basis == expected.per_game_basis == "projected_games"


def test_the_points_are_our_coefficients_applied_to_the_imported_line(priced, projection_csv):
    _import(priced, projection_csv, dry_run=False)

    stored = _stored(priced, JOKIC_ID)

    assert stored.projected_games == 73.0
    assert stored.per_game_stats["PTS"] == 28.4
    assert stored.raw_stats["PTS"] == 2073.2
    assert stored.fantasy_points_per_game == pytest.approx(JOKIC_POINTS_PER_GAME)
    assert stored.fantasy_points_total == pytest.approx(JOKIC_POINTS_PER_GAME * 73.0)


def test_a_row_with_no_games_count_still_ranks_on_its_per_game_line(priced, projection_csv):
    """Curry's row has a blank GP. Same fallback ladder ESPN's zero-game players take."""
    _import(priced, projection_csv, dry_run=False)

    stored = _stored(priced, CURRY_ID)

    assert stored.projected_games is None
    assert stored.per_game_basis == "per_game_stats"
    assert stored.fantasy_points_per_game > 0
    # No games count means no honest season total; 0.0 says so rather than inventing 82 games.
    assert stored.raw_stats == {}
    assert stored.fantasy_points_total == 0.0


def test_a_season_basis_file_and_a_per_game_file_price_the_same_player_the_same(priced):
    per_game = "Player,GP,PTS,REB,AST\nNikola Jokic,73,28.4,12.9,10.4\n"
    totals = "Player,GP,PTS,REB,AST\nNikola Jokic,73,2073.2,941.7,759.2\n"

    _import(priced, per_game, dry_run=False, options={"basis": "per_game"})
    from_per_game = _stored(priced, JOKIC_ID).fantasy_points_total
    _import(priced, totals, dry_run=False, options={"basis": "season"})
    from_totals = _stored(priced, JOKIC_ID)

    assert from_totals.fantasy_points_total == pytest.approx(from_per_game)
    assert from_totals.per_game_stats["PTS"] == 28.4


def test_an_unknown_basis_is_refused_and_writes_nothing(priced, projection_csv):
    with pytest.raises(ImportParseError, match="basis"):
        _import(priced, projection_csv, dry_run=False, options={"basis": "per_minute"})

    assert priced.scalar(select(func.count()).select_from(Projection)) == 0


def test_without_our_scoring_rules_an_import_refuses_rather_than_storing_zeroes(
    players, projection_csv
):
    """`players` has no league settings — the state of a checkout that never ran `make sync`."""
    with pytest.raises(ScoringRulesNotLoaded, match="sync"):
        _import(players, projection_csv, dry_run=False)

    assert players.scalar(select(func.count()).select_from(Projection)) == 0


# --- the pipeline, as this kind uses it ---------------------------------------------------


def test_every_row_is_accounted_for_and_the_names_resolve(priced, projection_csv):
    summary = _import(priced, projection_csv)

    assert summary.rows_parsed == len(summary.rows) == 16
    assert {
        row.source_name: row.player_name for row in summary.of_status(STATUS_MATCHED)
    } == EXPECTED_MATCHED
    assert {row.source_name for row in summary.of_status(STATUS_UNMATCHED)} == {
        "Nikola Topić",
        "Zaccharie Risacher",
    }
    (invalid,) = summary.of_status(STATUS_INVALID)
    assert invalid.source_name == "Mark Sears" and "PTS" in invalid.note
    assert summary.duplicate == 1


def test_a_dry_run_previews_the_real_counts_and_writes_nothing(priced, projection_csv):
    summary = _import(priced, projection_csv)

    assert summary.options == {}
    assert summary.rows_created == summary.matched == 12
    assert summary.rows_updated == summary.rows_unchanged == 0
    assert priced.scalar(select(func.count()).select_from(Projection)) == 0


def test_strict_holds_the_fuzzy_match_for_confirmation(priced, projection_csv):
    summary = _import(priced, projection_csv, accept=accept_only_certain)

    assert {row.source_name for row in summary.of_status(STATUS_REVIEW)} == {"Victor Wembanyma"}


def test_reimporting_the_same_file_creates_no_duplicates(priced, projection_csv):
    first = _import(priced, projection_csv, dry_run=False, options={"basis": "per_game"})
    second = _import(priced, projection_csv, dry_run=False, options={"basis": "per_game"})

    assert first.rows_created == 12
    assert second.options == {"basis": "per_game"}
    assert (second.rows_created, second.rows_updated, second.rows_unchanged) == (0, 0, 12)
    assert priced.scalar(select(func.count()).select_from(Projection)) == 12
    assert (
        priced.scalar(
            select(func.count()).select_from(Projection).where(Projection.player_id == SGA_ID)
        )
        == 1
    )


def test_a_moved_projection_updates_the_same_row_rather_than_adding_one(priced, projection_csv):
    """August's read replacing July's, which is what a re-import of a moved board is."""
    _import(priced, projection_csv, dry_run=False)
    before = _stored(priced, JOKIC_ID)
    row_id, was = before.id, before.fantasy_points_per_game

    summary = _import(priced, projection_csv.replace("28.4", "31.4"), dry_run=False)

    after = _stored(priced, JOKIC_ID)
    assert (summary.rows_updated, summary.rows_unchanged) == (1, 11)
    assert after.id == row_id
    assert after.fantasy_points_per_game > was
    assert after.raw_stats["PTS"] == 31.4 * 73


def test_an_imported_projection_never_claims_the_source_priced_it(priced, projection_csv):
    """ESPN publishes its points under *our* coefficients; an import doesn't, so it says so."""
    _import(priced, projection_csv, dry_run=False)

    assert _stored(priced, JOKIC_ID).source_fantasy_points_total is None
