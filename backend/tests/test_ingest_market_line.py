"""The `market_line` import kind: a long table of props -> lines, and lines -> a projection.

Three things are being asserted here, in this order:

1. **The file shape.** Long, not wide. Jokic appearing five times is five stats, not four
   duplicates, and (player, stat) is what can't repeat.
2. **The write.** One `market_line` row per (source, season, player, stat), updated in place
   when the odds move.
3. **The derivation.** Every touched player is re-priced under our stored coefficients into
   ONE `Projection` — which is how the market reaches the consensus board without a source
   adapter that knows sportsbooks exist (see `test_api_consensus`).
"""

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import MarketLine, Player, Projection
from app.espn.sync import ESPN_SOURCE, SEASON_PROJECTION_KIND
from app.ingest import (
    MARKET_PROJECTION_KIND,
    MARKET_SOURCE,
    STATUS_DUPLICATE,
    STATUS_INVALID,
    STATUS_MATCHED,
    STATUS_REVIEW,
    STATUS_UNMATCHED,
    UnknownStatError,
    price_lines,
    resolve_stat,
    run_import,
)
from app.ingest.market_line import market_row_key, validate_market_row
from app.ingest.parser import ParsedRow
from app.ranking.market import fair_value
from app.scoring import ScoringEngine, load_scoring_engine_for_season
from app.scoring.stats import STAT_NAME_TO_ID
from tests.conftest import LEAGUE_ID, SEASON

JOKIC = 3112335
SGA = 4278073
WEMBY = 5104157

# The shipped default. Pinned, so a locally-calibrated .env can't move these numbers.
SIGMA = 0.25


def _import(db, text, *, source=MARKET_SOURCE, season=SEASON, dry_run=False, **kwargs):
    return run_import(
        db,
        kind="market_line",
        source=source,
        season=season,
        text=text,
        dry_run=dry_run,
        **kwargs,
    )


def _lines(db, player_id, *, source=MARKET_SOURCE):
    return {
        row.stat_name: row
        for row in db.scalars(
            select(MarketLine).where(MarketLine.player_id == player_id, MarketLine.source == source)
        )
    }


def _naive(moment):
    """Drop the tzinfo. SQLite hands back naive datetimes; the writer stores aware ones."""
    return moment.replace(tzinfo=None)


def _market_projection(db, player_id, *, source=MARKET_SOURCE):
    return db.scalar(
        select(Projection).where(
            Projection.player_id == player_id,
            Projection.source == source,
            Projection.kind == MARKET_PROJECTION_KIND,
            Projection.season == SEASON,
        )
    )


# --- the stat cell --------------------------------------------------------------------------


@pytest.mark.parametrize("cell", ["AST", "ast", " Assists ", "assists", "apg", "Assists"], ids=repr)
def test_every_spelling_of_a_stat_lands_on_the_same_id(cell):
    """The cell and a projection file's HEADER are one vocabulary, not two that can drift."""
    assert resolve_stat(cell) == STAT_NAME_TO_ID["AST"]


def test_a_bare_stat_id_works_because_a_hand_kept_sheet_may_hold_one():
    assert resolve_stat(str(STAT_NAME_TO_ID["BLK"])) == STAT_NAME_TO_ID["BLK"]


@pytest.mark.parametrize("cell", ["Pts+Reb+Ast", "PRA", "FG%", "", None, "double double parlay"])
def test_a_stat_we_cannot_price_is_refused_with_what_we_do_understand(cell):
    with pytest.raises(UnknownStatError) as caught:
        resolve_stat(cell)
    message = str(caught.value)
    assert "PTS" in message and "AST" in message


def test_the_row_validator_turns_that_into_a_reason_rather_than_an_exception():
    bad = ParsedRow(line=1, name="Victor Wembanyama", values={"stat": "PRA", "line": 48.5})
    good = ParsedRow(line=2, name="Victor Wembanyama", values={"stat": "BLK", "line": 3.5})

    assert "PRA" in (validate_market_row(bad) or "")
    assert validate_market_row(good) is None


def test_the_row_key_is_the_stat_name_so_a_duplicate_note_reads():
    row = ParsedRow(line=1, name="Nikola Jokic", values={"stat": "points", "line": 27.5})

    assert market_row_key(row) == "PTS"


# --- the long file shape ---------------------------------------------------------------------


def test_one_player_with_five_props_is_five_rows_not_four_duplicates(priced, market_line_csv):
    """The whole reason `row_key` exists. Keyed on the player alone, four of these would die."""
    summary = _import(priced, market_line_csv)

    jokic_rows = [
        row
        for row in summary.rows
        if row.player_id == JOKIC and row.status in (STATUS_MATCHED, STATUS_DUPLICATE)
    ]
    assert [row.status for row in jokic_rows] == [STATUS_MATCHED] * 5 + [STATUS_DUPLICATE]
    assert sorted(_lines(priced, JOKIC)) == ["3PM", "AST", "PTS", "REB", "TO"]


def test_a_genuine_repeat_of_one_player_and_stat_is_still_a_duplicate(priced, market_line_csv):
    """Long format widens the key; it doesn't remove it. The FIRST row wins, as everywhere."""
    _import(priced, market_line_csv)

    rows = _import(priced, market_line_csv).rows
    duplicates = [row for row in rows if row.status == STATUS_DUPLICATE]
    assert len(duplicates) == 1
    assert duplicates[0].values["stat"] == "Points"
    assert "already resolved to this player and PTS" in duplicates[0].note
    # The line that got there first is the one stored — 27.5, not the 28.5 further down.
    assert _lines(priced, JOKIC)["PTS"].line == 27.5


def test_a_stat_nobody_can_price_costs_one_row_and_not_the_file(priced, market_line_csv):
    summary = _import(priced, market_line_csv)

    invalid = {row.values["stat"]: row for row in summary.rows if row.status == STATUS_INVALID}
    assert sorted(invalid) == ["PTS", "Pts+Reb+Ast"]
    assert "not a counting stat we can price" in invalid["Pts+Reb+Ast"].note
    # The row with no line at all is invalid for the older reason, and says which.
    assert "required column(s): line" in invalid["PTS"].note
    # The other nine rows landed regardless.
    assert summary.rows_created == 9


def test_the_columns_are_found_under_the_names_a_book_prints_them(priced, market_line_csv):
    summary = _import(priced, market_line_csv, dry_run=True)

    assert summary.columns == {
        "name": "Player",
        "stat": "Stat",
        "line": "Line",
        "over_odds": "Over",
        "under_odds": "Under",
    }


def test_an_over_under_header_is_the_line_and_not_a_price(priced):
    """ "Over/Under" contains `over_odds`' alias and IS the line's. Exact has to win."""
    text = "Player,Stat,Over/Under\nNikola Jokić,AST,9.5\n"

    summary = _import(priced, text, dry_run=True)

    assert summary.columns == {"name": "Player", "stat": "Stat", "line": "Over/Under"}


# --- what gets stored -------------------------------------------------------------------------


def test_a_line_is_stored_per_source_season_player_and_stat(priced, market_line_csv):
    _import(priced, market_line_csv)

    assists = _lines(priced, JOKIC)["AST"]
    assert (assists.source, assists.season) == (MARKET_SOURCE, SEASON)
    assert (assists.line, assists.over_odds, assists.under_odds) == (9.5, -150, 120)
    # An unpriced line stores nulls rather than an invented -110, so "no price" stays tellable
    # apart from "an even price" later.
    rebounds = _lines(priced, JOKIC)["REB"]
    assert (rebounds.over_odds, rebounds.under_odds) == (None, None)


def test_changed_odds_update_that_one_stat_in_place(priced, market_line_csv):
    """Misha's actual ask: "update the odds if they change" — one row, not a second one."""
    _import(priced, market_line_csv)
    before = _lines(priced, JOKIC)["AST"]
    original_id, original_as_of = before.id, before.as_of

    summary = _import(priced, "Player,Stat,Line,Over,Under\nNikola Jokić,Assists,9.5,-190,+155\n")

    after = _lines(priced, JOKIC)["AST"]
    assert summary.rows_created == 0 and summary.rows_updated == 1
    assert after.id == original_id  # the same row, rewritten
    assert (after.over_odds, after.under_odds) == (-190, 155)
    assert _naive(after.as_of) > _naive(original_as_of)
    # And the four stats the second file didn't mention are untouched.
    assert sorted(_lines(priced, JOKIC)) == ["3PM", "AST", "PTS", "REB", "TO"]


def test_re_importing_the_same_file_changes_nothing_and_says_so(priced, market_line_csv):
    _import(priced, market_line_csv)

    summary = _import(priced, market_line_csv)

    assert (summary.rows_created, summary.rows_updated) == (0, 0)
    assert summary.rows_unchanged == 9
    assert len(list(priced.scalars(select(MarketLine)))) == 9
    # And no second projection alongside the three already derived.
    derived = priced.scalars(select(Projection).where(Projection.source == MARKET_SOURCE)).all()
    assert len(derived) == 3


def test_a_second_book_is_a_second_set_of_lines_rather_than_an_overwrite(priced, market_line_csv):
    _import(priced, market_line_csv)

    _import(priced, "Player,Stat,Line\nNikola Jokić,Assists,10.5\n", source="draftkings")

    assert _lines(priced, JOKIC)["AST"].line == 9.5
    assert _lines(priced, JOKIC, source="draftkings")["AST"].line == 10.5
    assert _market_projection(priced, JOKIC, source="draftkings") is not None


def test_a_dry_run_previews_the_real_counts_and_writes_nothing(priced, market_line_csv):
    summary = _import(priced, market_line_csv, dry_run=True)

    assert summary.rows_created == 9
    # The derivation is previewed too, off the lines this file WOULD store.
    assert "3 created, 0 updated" in summary.notes[0]
    assert list(priced.scalars(select(MarketLine))) == []
    assert list(priced.scalars(select(Projection).where(Projection.source == MARKET_SOURCE))) == []


def test_a_mis_attributable_name_is_held_for_confirmation_rather_than_written(
    priced, market_line_csv
):
    """`accept_only_certain`: a line is a number you'd bet on, so a fuzzy hit waits for a human."""
    summary = _import(priced, market_line_csv)

    review = [row for row in summary.rows if row.status == STATUS_REVIEW]
    assert [row.source_name for row in review] == ["Victor Wembanyma"]
    assert review[0].candidates[0]["full_name"] == "Victor Wembanyama"
    assert "STL" not in _lines(priced, WEMBY)
    # And a name we carry nobody for is unmatched rather than held — nothing to decide.
    assert [row.source_name for row in summary.rows if row.status == STATUS_UNMATCHED] == [
        "Dalton Knecht"
    ]


# --- pricing a player's lines ------------------------------------------------------------------


def test_a_known_stat_line_scores_to_the_expected_fantasy_points(priced):
    """The worked example, under the league's real stored coefficients.

    PTS 3.0, REB 4.0, AST 4.0, 3PM 0.5, TO -2.0 (see GET /sync/league). Every line here is
    unpriced or evenly priced, so every fair value IS the line and the arithmetic is visible:
    27.5*3 + 12.5*4 + 9.5*4 + 1.5*0.5 + 3.5*-2 = 164.25.
    """
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)
    lines = {
        STAT_NAME_TO_ID["PTS"]: (27.5, None, None),
        STAT_NAME_TO_ID["REB"]: (12.5, -110, -110),
        STAT_NAME_TO_ID["AST"]: (9.5, None, None),
        STAT_NAME_TO_ID["3PM"]: (1.5, None, None),
        STAT_NAME_TO_ID["TO"]: (3.5, -110, -110),
    }

    derived = price_lines(lines, engine, sigma_fraction=SIGMA, games=70)

    assert derived.per_game_stats == {
        "PTS": 27.5,
        "REB": 12.5,
        "AST": 9.5,
        "3PM": 1.5,
        "TO": 3.5,
    }
    assert derived.fantasy_points_per_game == pytest.approx(164.25)
    assert derived.fantasy_points_total == pytest.approx(164.25 * 70)


def test_a_shaded_price_moves_the_stat_and_therefore_the_points(priced):
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)
    assists = STAT_NAME_TO_ID["AST"]
    even = price_lines({assists: (9.5, -110, -110)}, engine, sigma_fraction=SIGMA, games=70)
    shaded = price_lines({assists: (9.5, -150, 120)}, engine, sigma_fraction=SIGMA, games=70)

    assert even.per_game_stats["AST"] == 9.5
    assert shaded.per_game_stats["AST"] == pytest.approx(
        round(fair_value(9.5, -150, 120, sigma_fraction=SIGMA), 4)
    )
    # Assists pay 4, so a favoured over is worth four times the shift in fantasy points.
    assert shaded.fantasy_points_per_game > even.fantasy_points_per_game


def test_a_split_the_book_prices_but_never_totals_is_filled_in_exactly(priced):
    """OREB + DREB and no REB is the same case a projection export presents. One answer to it."""
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)
    lines = {
        STAT_NAME_TO_ID["OREB"]: (3.5, None, None),
        STAT_NAME_TO_ID["DREB"]: (8.5, None, None),
    }

    derived = price_lines(lines, engine, sigma_fraction=SIGMA, games=70)

    assert derived.per_game_stats["REB"] == 12.0


def test_a_games_line_sets_the_games_count_rather_than_being_scored(priced):
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)
    lines = {
        STAT_NAME_TO_ID["PTS"]: (20.0, None, None),
        STAT_NAME_TO_ID["GP"]: (65.0, None, None),
    }

    derived = price_lines(lines, engine, sigma_fraction=SIGMA, games=65.0)

    assert "GP" not in derived.per_game_stats
    assert derived.fantasy_points_per_game == pytest.approx(60.0)


def test_a_player_with_one_prop_gets_a_projection_worth_only_that_prop(priced):
    """Partial by construction, and documented as such: the market's opinion, not a projection."""
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)

    points = {STAT_NAME_TO_ID["PTS"]: (20.0, None, None)}

    derived = price_lines(points, engine, sigma_fraction=SIGMA, games=70)

    assert derived.per_game_stats == {"PTS": 20.0}
    assert derived.fantasy_points_per_game == pytest.approx(60.0)


# --- the derived projection ---------------------------------------------------------------------


def test_a_multi_stat_player_derives_exactly_one_projection_row(priced, market_line_csv):
    _import(priced, market_line_csv)

    rows = list(
        priced.scalars(
            select(Projection).where(
                Projection.player_id == JOKIC, Projection.source == MARKET_SOURCE
            )
        )
    )
    assert len(rows) == 1
    assert rows[0].kind == MARKET_PROJECTION_KIND
    assert rows[0].season == SEASON
    assert set(rows[0].per_game_stats) == {"PTS", "REB", "AST", "TO", "3PM"}
    # Per game in both, deliberately: a prop is quoted per game, so there is no season line
    # underneath it to store. See `derive_market_projections`.
    assert rows[0].raw_stats == rows[0].per_game_stats
    assert rows[0].per_game_basis == "per_game_stats"


def test_three_players_with_lines_derive_three_projections(priced, market_line_csv):
    _import(priced, market_line_csv)

    rows = priced.scalars(select(Projection).where(Projection.source == MARKET_SOURCE))
    assert {row.player_id for row in rows} == {JOKIC, SGA, WEMBY}


def test_changing_one_players_odds_re_derives_him_and_nobody_else(priced, market_line_csv):
    _import(priced, market_line_csv)
    before = {
        player_id: _market_projection(priced, player_id).fantasy_points_per_game
        for player_id in (JOKIC, SGA, WEMBY)
    }

    summary = _import(priced, "Player,Stat,Line,Over,Under\nNikola Jokić,Assists,9.5,-400,+300\n")

    after = {
        player_id: _market_projection(priced, player_id).fantasy_points_per_game
        for player_id in (JOKIC, SGA, WEMBY)
    }
    assert after[JOKIC] > before[JOKIC]  # a heavily favoured over lifts a 4-point stat
    assert after[SGA] == before[SGA]
    assert after[WEMBY] == before[WEMBY]
    assert "1 created, 0 updated" not in summary.notes[0]
    assert "0 created, 1 updated" in summary.notes[0]


def test_the_season_total_leans_on_espns_games_count_when_we_have_one(db, synced, market_line_csv):
    """The board ranks on per-game, so games only set the displayed total — but use the real one."""
    espn_games = db.scalar(
        select(Projection.projected_games).where(
            Projection.player_id == JOKIC,
            Projection.source == ESPN_SOURCE,
            Projection.kind == SEASON_PROJECTION_KIND,
        )
    )
    assert espn_games  # the fixture really does carry one

    _import(db, market_line_csv)

    row = _market_projection(db, JOKIC)
    assert row.projected_games == espn_games
    assert row.fantasy_points_total == pytest.approx(
        row.fantasy_points_per_game * espn_games, rel=1e-6
    )


def test_a_player_espn_has_no_games_for_falls_back_to_the_configured_default(
    priced, market_line_csv
):
    _import(priced, market_line_csv)

    assert _market_projection(priced, JOKIC).projected_games == get_settings().market_default_games


def test_the_dispersion_dial_re_prices_the_derived_projection(priced, market_line_csv, monkeypatch):
    """MARKET_SIGMA_FRAC is a real dial: turn it up, the shaded lines move further."""
    _import(priced, market_line_csv)
    moderate = _market_projection(priced, SGA).fantasy_points_per_game

    monkeypatch.setattr(get_settings(), "market_sigma_frac", 0.5)
    _import(priced, market_line_csv)
    wide = _market_projection(priced, SGA).fantasy_points_per_game

    # SGA's points line is priced -135/+110 — a favoured over — so a wider sigma lifts it more.
    assert wide > moderate


def test_the_market_projection_is_priced_by_the_same_engine_espns_is(priced, market_line_csv):
    """Not a second scoring path: the stored stat line, re-scored, has to give the same number."""
    _import(priced, market_line_csv)
    row = _market_projection(priced, JOKIC)
    engine = load_scoring_engine_for_season(priced, SEASON, espn_league_id=LEAGUE_ID)

    assert isinstance(engine, ScoringEngine)
    assert engine.score(row.per_game_stats) == pytest.approx(row.fantasy_points_per_game)


def test_an_import_that_resolved_nothing_writes_nothing_and_derives_nothing(priced):
    summary = _import(priced, "Player,Stat,Line\nDalton Knecht,PTS,14.5\n")

    assert summary.rows_created == 0
    assert list(priced.scalars(select(MarketLine))) == []
    assert list(priced.scalars(select(Projection).where(Projection.source == MARKET_SOURCE))) == []
    assert summary.notes == []


def test_an_import_with_no_scoring_rules_refuses_rather_than_storing_zeroes(
    players, market_line_csv
):
    """`players` has the pool and no league settings — the same guard a projection import has."""
    from app.scoring import ScoringRulesNotLoaded

    with pytest.raises(ScoringRulesNotLoaded):
        _import(players, market_line_csv)

    assert list(players.scalars(select(MarketLine))) == []


def test_every_player_with_lines_is_one_of_ours(priced, market_line_csv):
    _import(priced, market_line_csv)

    for row in priced.scalars(select(MarketLine)):
        assert priced.get(Player, row.player_id) is not None
