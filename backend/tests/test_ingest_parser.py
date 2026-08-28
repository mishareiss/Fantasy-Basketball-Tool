"""The CSV/paste parser: it finds its columns instead of demanding them."""

import pytest

from app.ingest import (
    ADP_COLUMNS,
    ImportParseError,
    detect_columns,
    normalize_header,
    parse_number,
    parse_table,
    split_positions,
)
from app.ingest.parser import PARSE_TEXT, ValueColumn, parse_text, sniff_delimiter

# Enough of a kind to parse against, without dragging the registry in.
VALUE_COLUMNS = ADP_COLUMNS


def test_finds_every_column_under_an_alias(adp_csv):
    table = parse_table(adp_csv, VALUE_COLUMNS)

    assert table.columns.as_dict() == {
        "name": "PLAYER",
        "team": "Tm",
        "positions": "Pos",
        "adp": "Avg Pick",
        "auction_value": "$",
        "percent_owned": "%Own",
    }


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Player", "name"),
        ("PLAYER NAME", "name"),
        ("Full Name", "name"),
        ("Tm", "team"),
        ("NBA Team", "team"),
        ("Pos", "positions"),
        ("Position", "positions"),
    ],
)
def test_role_columns_are_recognized_however_they_are_spelled(header, expected):
    # A name column always has to exist, so the header under test joins a minimal file.
    headers = [header, "ADP"] if expected == "name" else ["Player", header, "ADP"]

    columns = detect_columns(headers, VALUE_COLUMNS)

    assert columns.as_dict()[expected] == header


@pytest.mark.parametrize("header", ["ADP", "Avg Pick", "Average Draft Position", "Rank", "OVR"])
def test_the_value_column_is_recognized_however_it_is_spelled(header):
    assert detect_columns(["Player", header], VALUE_COLUMNS).as_dict()["adp"] == header


def test_an_exact_header_beats_one_that_merely_contains_the_alias():
    """A file with both "Rank" and "Rank Change" must not resolve ADP to the second one."""
    columns = detect_columns(["Player", "Rank Change", "Rank"], VALUE_COLUMNS)

    assert columns.as_dict()["adp"] == "Rank"


def test_a_quoted_field_keeps_its_comma(adp_csv):
    """ "Gilgeous-Alexander, Shai" is one name, not two columns — the reason to use `csv`."""
    rows = parse_table(adp_csv, VALUE_COLUMNS).rows

    assert rows[0].name == "Gilgeous-Alexander, Shai"
    assert rows[0].values["adp"] == 1.2
    assert next(row for row in rows if row.name == "Cooper Flagg").positions == ("SF", "PF")


def test_messy_whitespace_is_trimmed_and_blank_rows_are_skipped(adp_csv):
    table = parse_table(adp_csv, VALUE_COLUMNS)

    giannis = next(row for row in table.rows if "Giannis" in row.name)
    assert giannis.name == "Giannis  Antetokounmpo"  # internal spacing is the matcher's problem
    assert giannis.team == "MIL"
    assert table.skipped_blank == 1
    assert all(row.name.strip() == row.name for row in table.rows)


def test_a_spreadsheet_paste_is_tab_delimited_and_that_is_noticed():
    pasted = "Player\tTeam\tADP\nNikola Jokic\tDEN\t2.4\nLuka Doncic\tDAL\t4.0\n"

    table = parse_table(pasted, VALUE_COLUMNS)

    assert table.delimiter == "\t"
    assert [row.name for row in table.rows] == ["Nikola Jokic", "Luka Doncic"]
    assert table.rows[0].team == "DEN"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Player,ADP\nA,1\n", ","),
        ("Player\tADP\nA\t1\n", "\t"),
        ("Player;ADP\nA;1\n", ";"),
        ("Player|ADP\nA|1\n", "|"),
    ],
)
def test_delimiters_are_sniffed(text, expected):
    assert sniff_delimiter(text) == expected


def test_an_explicit_column_map_wins_over_detection():
    """The one file a year the alias table can't read: point it at the columns by hand."""
    text = "Guy,Slot,Notes\nNikola Jokic,2.4,best passer alive\n"

    table = parse_table(text, VALUE_COLUMNS, overrides={"name": "Guy", "adp": "Slot"})

    assert table.columns.as_dict() == {"name": "Guy", "adp": "Slot"}
    assert table.rows[0].name == "Nikola Jokic"
    assert table.rows[0].values["adp"] == 2.4


def test_a_column_map_can_point_at_a_column_number():
    text = "A,B,C\nNikola Jokic,x,2.4\n"

    table = parse_table(text, VALUE_COLUMNS, overrides={"name": "1", "adp": "3"})

    assert table.rows[0].name == "Nikola Jokic"
    assert table.rows[0].values["adp"] == 2.4


def test_a_column_map_pointing_nowhere_says_so():
    with pytest.raises(ImportParseError, match="matches no column"):
        parse_table("Player,ADP\nA,1\n", VALUE_COLUMNS, overrides={"adp": "Nope"})


def test_no_name_column_is_an_error_that_says_what_it_looked_for():
    with pytest.raises(ImportParseError) as caught:
        parse_table("Rank,Team\n1,DEN\n", VALUE_COLUMNS)

    assert "no player-name column" in str(caught.value)
    assert "column map" in str(caught.value)


def test_a_missing_required_value_column_is_an_error():
    """A file with names and nothing else isn't an ADP import, however friendly we're being."""
    with pytest.raises(ImportParseError, match="required value"):
        parse_table("Player,Team\nNikola Jokic,DEN\n", VALUE_COLUMNS)


def test_optional_value_columns_are_simply_absent():
    table = parse_table("Player,ADP\nNikola Jokic,2.4\n", VALUE_COLUMNS)

    assert table.columns.as_dict() == {"name": "Player", "adp": "ADP"}
    assert table.rows[0].values == {"adp": 2.4}


def test_an_empty_paste_is_an_error_not_an_empty_import():
    with pytest.raises(ImportParseError, match="empty"):
        parse_table("   \n\n", VALUE_COLUMNS)


def test_a_ragged_row_is_read_as_far_as_it_goes():
    """Exports truncate trailing empty cells all the time; that isn't a broken file."""
    table = parse_table("Player,ADP,$\nNikola Jokic,2.4\n", VALUE_COLUMNS)

    assert table.rows[0].values == {"adp": 2.4, "auction_value": None}


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("12", 12.0),
        ("2.4", 2.4),
        ("$68", 68.0),
        ("99.9%", 99.9),
        ("1,234", 1234.0),
        ("#4", 4.0),
        ("12th", 12.0),
        ("(3)", -3.0),
        (" 7 ", 7.0),
        ("", None),
        ("-", None),
        ("N/A", None),
        ("undrafted", None),
        ("best passer alive", None),
    ],
)
def test_a_number_is_read_through_whatever_decorates_it(cell, expected):
    assert parse_number(cell) == expected


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("PG", ("PG",)),
        ("PG/SG", ("PG", "SG")),
        ("SF, PF", ("SF", "PF")),
        ("c", ("C",)),
        ("PG-SG", ("PG-SG",)),
        ("", ()),
    ],
)
def test_positions_split_on_whatever_separates_them(cell, expected):
    assert split_positions(cell) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("% Owned", "% owned"),
        ("Player_Name", "player name"),
        ("  ADP.  ", "adp"),
        ("$", "$"),
        # "#" survives folding, because on a ranking export it *is* the rank column's whole
        # name — folded away it would normalize to "" and match no alias ever.
        ("#", "#"),
        ("Rank #", "rank #"),
        ("", ""),
    ],
)
def test_headers_normalize_to_the_form_the_alias_tables_use(header, expected):
    assert normalize_header(header) == expected


# --- text value columns ---------------------------------------------------------------------

TIER_COLUMNS = (
    ValueColumn("adp", ("adp",), required=True),
    ValueColumn("tier", ("tier",), parse_value=PARSE_TEXT),
)


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("Tier 2", "Tier 2"),
        (" Elite ", "Elite"),
        ("1", "1"),
        ("--", None),
        ("n/a", None),
        ("", None),
    ],
)
def test_a_text_column_keeps_the_label_and_drops_the_nullish_ones(cell, expected):
    assert parse_text(cell) == expected


def test_a_text_column_is_read_as_text_while_the_rest_stay_numbers():
    """A tier of "3" is the string "3": half of sources name their tiers, and one file can't
    be read two ways depending on which rows happen to be numeric."""
    table = parse_table("Player,ADP,Tier\nNikola Jokic,2.4,3\nCooper Flagg,6,Elite\n", TIER_COLUMNS)

    assert table.rows[0].values == {"adp": 2.4, "tier": "3"}
    assert table.rows[1].values == {"adp": 6.0, "tier": "Elite"}


# --- file order -------------------------------------------------------------------------------


def test_rows_carry_their_place_in_the_file_not_just_their_line_number(adp_csv):
    """`index` counts data rows; `line` counts text lines. A blank line separates the two, and
    a ranking with no rank column ranks by the first."""
    table = parse_table(adp_csv, VALUE_COLUMNS)

    assert [row.index for row in table.rows] == list(range(1, len(table.rows) + 1))
    giannis = next(row for row in table.rows if "Giannis" in row.name)
    assert (giannis.index, giannis.line) == (3, 5)


def test_a_row_reports_which_required_values_it_lacks():
    table = parse_table("Player,ADP\nMark Sears,\n", VALUE_COLUMNS)

    assert table.rows[0].missing(VALUE_COLUMNS) == ["adp"]
    assert table.rows[0].missing([ValueColumn("adp", ("adp",))]) == []
