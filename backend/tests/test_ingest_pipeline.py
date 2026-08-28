"""The import pipeline: dry runs write nothing, commits are idempotent, aliases stick."""

import pytest
from sqlalchemy import func, select

from app.db.models import AdpEntry, Player, PlayerAlias
from app.ingest import (
    PLANNED_KINDS,
    STATUS_DUPLICATE,
    STATUS_INVALID,
    STATUS_MATCHED,
    STATUS_REVIEW,
    STATUS_UNMATCHED,
    UnknownKindError,
    accept_only_certain,
    get_kind,
    kind_names,
    run_import,
)
from app.matching import record_alias
from tests.conftest import SEASON

SOURCE = "hashtag"

# Who the fixture CSV is about, by outcome. Written out rather than counted, so a change in
# matching behaviour fails a test that says which player moved.
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
    "DJ Carton": "D.J. Carton",
}
EXPECTED_UNMATCHED = {"Nikola Topić", "Zaccharie Risacher"}


def _import(db, csv, **kwargs):
    kwargs.setdefault("season", SEASON)
    return run_import(db, kind="adp", source=SOURCE, text=csv, **kwargs)


def _counts(db) -> tuple[int, int]:
    return (
        db.scalar(select(func.count()).select_from(AdpEntry)),
        db.scalar(select(func.count()).select_from(PlayerAlias)),
    )


def test_every_row_is_accounted_for(players, adp_csv):
    summary = _import(players, adp_csv)

    assert summary.rows_parsed == len(summary.rows) == 15
    assert (
        summary.matched + summary.review + summary.unmatched + summary.duplicate + (summary.invalid)
        == summary.rows_parsed
    )


def test_names_resolve_through_accents_inversion_suffixes_and_a_typo(players, adp_csv):
    """One pass over the fixture, one assertion per way a source can misspell a name."""
    summary = _import(players, adp_csv)

    assert {
        row.source_name: row.player_name for row in summary.of_status(STATUS_MATCHED)
    } == EXPECTED_MATCHED
    methods = {row.source_name: row.method for row in summary.of_status(STATUS_MATCHED)}
    assert methods["Cooper Flagg"] == "exact"
    assert methods["Nikola Jokić"] == "normalized"  # an accent is not a hard match
    assert methods["Towns, Karl-Anthony"] == "exact"  # "Last, First" is uninverted first
    assert methods["Michael Porter"] == "normalized"  # the source dropped the Jr.
    assert methods["DJ Carton"] == "normalized"  # same letters, different punctuation
    assert methods["Victor Wembanyma"] == "fuzzy"  # a typo is the only genuine guess here


def test_a_name_we_carry_nobody_for_comes_back_as_the_worklist(players, adp_csv):
    summary = _import(players, adp_csv)

    assert {row.source_name for row in summary.of_status(STATUS_UNMATCHED)} == EXPECTED_UNMATCHED
    assert all(row.player_id is None for row in summary.of_status(STATUS_UNMATCHED))
    assert {row.source_name for row in summary.worklist} == EXPECTED_UNMATCHED


def test_a_row_with_no_adp_is_invalid_rather_than_stored_as_null(players, adp_csv):
    summary = _import(players, adp_csv)

    (invalid,) = summary.of_status(STATUS_INVALID)
    assert invalid.source_name == "Mark Sears"
    assert "adp" in invalid.note


def test_the_same_player_twice_in_one_file_is_imported_once(players, adp_csv):
    summary = _import(players, adp_csv)

    (duplicate,) = summary.of_status(STATUS_DUPLICATE)
    assert duplicate.source_name == "Shai Gilgeous-Alexander"
    assert "line 2" in duplicate.note
    assert duplicate.values["adp"] == 1.4  # the later row's number is the one dropped


def test_a_dry_run_writes_absolutely_nothing(players, adp_csv):
    summary = _import(players, adp_csv, dry_run=True)

    assert summary.dry_run
    assert _counts(players) == (0, 0)
    # ...and still says exactly what it would have done.
    assert summary.rows_created == summary.matched == 11
    assert summary.rows_updated == summary.rows_unchanged == 0
    assert summary.aliases_created == 11


def test_a_commit_writes_the_rows_and_the_values(players, adp_csv):
    summary = _import(players, adp_csv, dry_run=False)

    assert summary.rows_created == 11
    assert _counts(players) == (11, 11)

    sga = players.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073))
    assert (sga.source, sga.season) == (SOURCE, SEASON)
    assert (sga.adp, sga.auction_value, sga.percent_owned) == (1.2, 68.0, 99.9)


def test_re_importing_the_same_file_creates_no_duplicates(players, adp_csv):
    first = _import(players, adp_csv, dry_run=False)
    second = _import(players, adp_csv, dry_run=False)

    assert (second.rows_created, second.rows_updated) == (0, 0)
    assert second.rows_unchanged == first.rows_created
    assert (second.aliases_created, second.aliases_existing) == (0, first.aliases_created)
    assert _counts(players) == (11, 11)


def test_a_dry_run_of_an_already_imported_file_says_so_before_you_commit(players, adp_csv):
    _import(players, adp_csv, dry_run=False)

    preview = _import(players, adp_csv, dry_run=True)

    assert (preview.rows_created, preview.rows_updated) == (0, 0)
    assert preview.rows_unchanged == 11
    assert (preview.aliases_created, preview.aliases_existing) == (0, 11)


def test_a_moved_adp_is_an_update_not_a_second_row(players, adp_csv):
    _import(players, adp_csv, dry_run=False)

    summary = _import(players, adp_csv.replace(",1.2,", ",9.9,"), dry_run=False)

    assert (summary.rows_created, summary.rows_updated) == (0, 1)
    assert players.scalar(select(AdpEntry).where(AdpEntry.player_id == 4278073)).adp == 9.9
    assert _counts(players) == (11, 11)


def test_a_rank_only_file_does_not_wipe_the_auction_values_we_already_have(players, adp_csv):
    """A column the file doesn't carry is left alone — importing less must not delete more."""
    _import(players, adp_csv, dry_run=False)

    _import(players, "Player,Rank\nCooper Flagg,7\n", dry_run=False)

    flagg = players.scalar(select(AdpEntry).where(AdpEntry.player_id == 5041939))
    assert flagg.adp == 7.0
    assert flagg.auction_value == 55.0


def test_the_fuzzy_match_becomes_an_alias_so_the_next_import_reads_it(players, adp_csv):
    """The point of recording matches: the second run doesn't re-guess, it looks it up."""
    _import(players, adp_csv, dry_run=False)

    alias = players.scalar(
        select(PlayerAlias).where(
            PlayerAlias.source == SOURCE, PlayerAlias.source_name == "Victor Wembanyma"
        )
    )
    assert alias.player_id == 5104157
    assert alias.match_method == "fuzzy"  # provenance is how we FIRST decided, not "alias"
    assert 0.88 <= alias.confidence < 1.0

    second = _import(players, adp_csv)
    wemby = next(row for row in second.rows if row.source_name == "Victor Wembanyma")
    assert (wemby.method, wemby.confidence) == ("alias", 1.0)


def test_a_hand_made_alias_resolves_a_name_no_matcher_could(players, adp_csv):
    """The whole escape hatch, end to end: POST /players/{id}/aliases then re-import."""
    before = _import(players, adp_csv, dry_run=False)
    assert "Nikola Topić" in {row.source_name for row in before.of_status(STATUS_UNMATCHED)}

    # What POST /players/{espn_player_id}/aliases does. Pretend Topic is our Josh Giddey.
    record_alias(
        players,
        source=SOURCE,
        source_name="Nikola Topić",
        player_id=4871145,
        confidence=1.0,
        match_method="manual",
        restate_provenance=True,
    )
    players.commit()

    after = _import(players, adp_csv, dry_run=False)

    resolved = next(row for row in after.rows if row.source_name == "Nikola Topić")
    assert (resolved.status, resolved.method) == (STATUS_MATCHED, "alias")
    assert resolved.player_id == 4871145
    assert after.rows_created == 1
    assert players.scalar(select(AdpEntry).where(AdpEntry.player_id == 4871145)).adp == 200.0


def test_a_strict_kind_holds_the_fuzzy_row_for_confirmation(players, adp_csv):
    """Same file, stricter policy: the typo becomes a review row with its candidates."""
    summary = _import(players, adp_csv, accept=accept_only_certain)

    (review,) = summary.of_status(STATUS_REVIEW)
    assert review.source_name == "Victor Wembanyma"
    assert review.candidates[0]["full_name"] == "Victor Wembanyama"
    assert summary.matched == 10
    assert _counts(players) == (0, 0)


def test_a_strict_commit_writes_only_the_certain_rows(players, adp_csv):
    summary = _import(players, adp_csv, dry_run=False, accept=accept_only_certain)

    assert summary.rows_created == 10
    assert _counts(players) == (10, 10)
    assert players.scalar(select(AdpEntry).where(AdpEntry.player_id == 5104157)) is None


def test_two_of_our_players_sharing_a_name_are_reviewed_not_guessed_at(players, adp_csv):
    """Attaching a number to the wrong Cooper Flagg is worse than leaving the row unresolved."""
    players.add(
        Player(espn_player_id=999999, full_name="Cooper Flagg", nba_team="SAC", positions=["PF"])
    )
    players.commit()

    summary = _import(players, "Player,ADP\nCooper Flagg,6.0\n")

    (review,) = summary.of_status(STATUS_REVIEW)
    assert review.method == "ambiguous"
    assert review.player_id is None
    assert {candidate["player_id"] for candidate in review.candidates} == {5041939, 999999}


def test_the_team_column_breaks_that_tie(players):
    """Which is why the parser bothers finding the team column at all."""
    players.add(
        Player(espn_player_id=999999, full_name="Cooper Flagg", nba_team="SAC", positions=["PF"])
    )
    players.commit()

    summary = _import(players, "Player,Team,ADP\nCooper Flagg,DAL,6.0\n")

    (matched,) = summary.of_status(STATUS_MATCHED)
    assert matched.player_id == 5041939


def test_two_seasons_of_adp_for_one_player_coexist(players, adp_csv):
    """Where the room had a 22-year-old last August is the dynasty signal; don't destroy it."""
    _import(players, adp_csv, season=SEASON - 1, dry_run=False)
    summary = _import(players, adp_csv, season=SEASON, dry_run=False)

    assert summary.rows_created == 11  # a new season is new rows, not an update
    assert summary.rows_updated == 0
    assert _counts(players) == (22, 11)  # ...on the same 11 aliases

    seasons = players.scalars(
        select(AdpEntry.season).where(AdpEntry.player_id == 4278073).order_by(AdpEntry.season)
    )
    assert list(seasons) == [SEASON - 1, SEASON]


def test_two_sources_of_adp_for_one_player_coexist(players, adp_csv):
    _import(players, adp_csv, dry_run=False)
    run_import(
        db=players,
        kind="adp",
        source="fantasypros",
        season=SEASON,
        text="Player,ADP\nCooper Flagg,4.0\n",
        dry_run=False,
    )

    entries = {
        entry.source: entry.adp
        for entry in players.scalars(select(AdpEntry).where(AdpEntry.player_id == 5041939))
    }
    assert entries == {SOURCE: 6.0, "fantasypros": 4.0}


def test_an_import_needs_a_source_to_attribute_the_rows_to(players, adp_csv):
    with pytest.raises(ValueError, match="source"):
        run_import(players, kind="adp", source="  ", season=SEASON, text=adp_csv)


def test_an_unknown_kind_names_the_ones_that_exist_and_the_ones_planned(players, adp_csv):
    with pytest.raises(UnknownKindError) as caught:
        run_import(players, kind="market_line", source=SOURCE, season=SEASON, text=adp_csv)

    message = caught.value.args[0]
    assert "adp" in message
    assert "projection" in message
    assert "ranking" in message
    assert "market_line" in message


def test_the_registry_holds_the_built_kinds_and_documents_what_is_deferred():
    assert kind_names() == ["adp", "projection", "ranking"]
    assert get_kind("ADP").name == "adp"  # the kind name is case-insensitive
    assert get_kind("adp").required_fields == ("adp",)
    assert get_kind("projection").required_fields == ("PTS",)
    assert get_kind("ranking").required_fields == ()
    assert set(PLANNED_KINDS) == {"market_line"}
    assert all(note.strip() for note in PLANNED_KINDS.values())
