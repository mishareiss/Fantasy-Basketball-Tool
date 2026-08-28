"""Turn a pasted table or an uploaded CSV into rows we can match, forgivingly.

Nobody is going to hand-edit a FantasyPros export into our column order two weeks before a
draft, and no two sources agree on what to call a column: "Player" / "Name" / "PLAYER NAME",
"Tm" / "Team", "ADP" / "AVG" / "Rank". So the parser *finds* its columns instead of demanding
them, via a small alias table per role, and takes an explicit override for the one file a year
that defeats it.

Deliberately stdlib `csv` only. It already handles the two things that actually break naive
splitting — quoted fields containing the delimiter ("Jokic, Nikola") and embedded newlines —
and a paste from a spreadsheet is just a tab-delimited CSV.
"""

import csv
import io
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# Header roles every kind needs. The value columns are per-kind and come from the registry.
NAME_FIELD = "name"
TEAM_FIELD = "team"
POSITIONS_FIELD = "positions"

# Aliases are matched against a normalized header ("% Owned" -> "percent owned", "PLAYER_NAME"
# -> "player name"), so this table only carries genuinely different words, not spellings.
NAME_ALIASES = (
    "player",
    "name",
    "player name",
    "full name",
    "players",
    "playername",
)
TEAM_ALIASES = ("team", "tm", "nba team", "pro team", "teams")
POSITION_ALIASES = ("pos", "position", "positions", "elig")

_NON_ALNUM = re.compile(r"[^0-9a-z%$#]+")
_WHITESPACE = re.compile(r"\s+")
# What a source writes when it means "no number here".
_NULLISH = frozenset({"", "-", "--", "n/a", "na", "none", "null", "nr", "und", "undrafted"})
# Everything that decorates a number in an export: $12, 45.3%, 1,234, (2), "12 "
_NUMBER_NOISE = re.compile(r"[,$%\s]")
# "#4" / "(3)" — decoration around a number, not part of it.
_DECORATION = re.compile(r"^[(#]+|[)]+$")
# "12th", "1st" — a rank column written as an ordinal.
_ORDINAL = re.compile(r"(?<=\d)(st|nd|rd|th)$", re.IGNORECASE)

# Delimiters worth considering, best first. Tab comes first because a spreadsheet paste is the
# most common input and is always tab-delimited.
CANDIDATE_DELIMITERS = ("\t", ",", ";", "|")


class ImportParseError(ValueError):
    """The text isn't a table we can read — raised with what we saw, so it's fixable."""


# How a value column's cells are read. Almost everything imported is a number; a tier is the
# exception, because sources label tiers as often as they number them ("1" / "Tier 2" /
# "Elite") and parsing "Elite" as a number throws the column away.
PARSE_NUMBER = "number"
PARSE_TEXT = "text"


@dataclass(frozen=True)
class ValueColumn:
    """One value a kind wants off each row, and what sources call it.

    `required=True` means a row without it is not importable at all (an ADP row with no ADP is
    just a name); the pipeline reports those separately rather than storing a null.
    """

    field: str
    aliases: tuple[str, ...]
    required: bool = False
    # PARSE_NUMBER (the default) or PARSE_TEXT. Kept on the column rather than guessed per
    # cell, so a tier of "3" stays the string "3" instead of becoming 3.0 in half the files.
    parse_value: str = PARSE_NUMBER


@dataclass(frozen=True)
class ColumnMap:
    """Which column index plays which role, and the header each was recognized by."""

    headers: tuple[str, ...]
    name: int
    team: int | None = None
    positions: int | None = None
    values: dict[str, int] = field(default_factory=dict)

    def header_for(self, index: int | None) -> str | None:
        if index is None or index >= len(self.headers):
            return None
        return self.headers[index]

    def as_dict(self) -> dict[str, str]:
        """`field -> the header it was found under`, for the import summary."""
        found = {NAME_FIELD: self.name, TEAM_FIELD: self.team, POSITIONS_FIELD: self.positions}
        found.update(self.values)
        return {
            key: header
            for key, index in found.items()
            if (header := self.header_for(index)) is not None
        }


@dataclass(frozen=True)
class ParsedRow:
    """One data row, with its columns pulled out. Nothing is matched or stored yet."""

    # 1-based line number in the source text, so a complaint points at something findable.
    line: int
    name: str
    team: str | None = None
    positions: tuple[str, ...] = ()
    values: dict[str, float | str | None] = field(default_factory=dict)
    # 1-based position among the file's *data* rows, ignoring the header and skipped blanks.
    # `line` points at the text; this is the row's place in the list, which is what a ranking
    # with no rank column is actually asserting. Distinct from the index among *accepted*
    # rows on purpose: a row held for review must leave a gap, not renumber everyone below it.
    index: int = 0

    def missing(self, columns: Iterable[ValueColumn]) -> list[str]:
        """Required value columns this row has no value for, number or label."""
        return [
            column.field
            for column in columns
            if column.required and self.values.get(column.field) is None
        ]


@dataclass(frozen=True)
class ParsedTable:
    """The whole parse: what the columns were, the rows, and what was thrown away."""

    columns: ColumnMap
    rows: tuple[ParsedRow, ...]
    delimiter: str
    # Blank lines and rows whose name cell was empty — counted, not reported one by one.
    skipped_blank: int = 0


def normalize_header(header: str) -> str:
    """Fold a header to the form the alias tables are written in.

    "% Owned" -> "% owned", "Player_Name" -> "player name", "ADP." -> "adp". `%`, `$` and `#`
    are kept because they carry the whole meaning in headers like "$" (auction value), "%Own"
    and "#" (the rank column on most ranking exports) — folded away, those headers normalize
    to the empty string and can never match an alias.
    """
    folded = _NON_ALNUM.sub(" ", (header or "").strip().lower())
    return _WHITESPACE.sub(" ", folded).strip()


def parse_number(text: str) -> float | None:
    """A number out of an export cell, or None if the cell doesn't hold one.

    >>> parse_number("$12")
    12.0
    >>> parse_number("45.3%")
    45.3
    >>> parse_number("1,234")
    1234.0
    >>> parse_number("--") is None
    True
    """
    cleaned = (text or "").strip()
    if cleaned.lower() in _NULLISH:
        return None
    cleaned = _NUMBER_NOISE.sub("", cleaned)
    # "(3)" is a negative in some exports, and "12th" / "#4" are ranks with decoration.
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = _ORDINAL.sub("", _DECORATION.sub("", cleaned))
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def parse_text(text: str) -> str | None:
    """A label out of an export cell, or None if the cell says "nothing here".

    The same `_NULLISH` vocabulary `parse_number` uses, so an empty tier and an empty ADP are
    both absent rather than one being the literal string "--".

    >>> parse_text(" Tier 2 ")
    'Tier 2'
    >>> parse_text("--") is None
    True
    """
    cleaned = (text or "").strip()
    if cleaned.lower() in _NULLISH:
        return None
    return _WHITESPACE.sub(" ", cleaned)


def split_positions(text: str) -> tuple[str, ...]:
    """ "PG/SG" -> ("PG", "SG"); "SF, PF" -> ("SF", "PF"). Order kept, duplicates dropped."""
    tokens = [
        token.strip().upper() for token in re.split(r"[/,;|\s]+", text or "") if token.strip()
    ]
    seen: dict[str, None] = {}
    for token in tokens:
        seen.setdefault(token, None)
    return tuple(seen)


def sniff_delimiter(text: str) -> str:
    """Pick the delimiter. `csv.Sniffer` first, then "whichever appears on every line".

    Sniffer is good but not infallible on two-column pastes, and it raises rather than
    guessing — so the fallback counts candidates across the first few lines and takes the one
    that is present and consistent. Comma is the last resort because a single-column list of
    names is still a valid (if boring) CSV.
    """
    sample = "\n".join(text.splitlines()[:10])
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS)).delimiter
    except csv.Error:
        pass

    lines = [line for line in text.splitlines()[:10] if line.strip()]
    for delimiter in CANDIDATE_DELIMITERS:
        counts = {line.count(delimiter) for line in lines}
        if counts and 0 not in counts and len(counts) == 1:
            return delimiter
    return ","


def _match_alias(headers: Sequence[str], aliases: Sequence[str]) -> int | None:
    """The first column whose header is one of these aliases, else the first that contains one.

    Exact-first matters: a file with both "Rank" and "Rank Change" must not resolve "rank" to
    the second one just because it came first.
    """
    normalized = [normalize_header(header) for header in headers]
    for alias in aliases:
        for index, header in enumerate(normalized):
            if header == alias:
                return index
    for alias in aliases:
        for index, header in enumerate(normalized):
            if header and alias in header.split():
                return index
    return None


def _resolve_override(headers: Sequence[str], wanted: str) -> int | None:
    """An override's target: a header (however spelled) or a 1-based column number."""
    normalized = [normalize_header(header) for header in headers]
    target = normalize_header(wanted)
    if target in normalized:
        return normalized.index(target)
    if target.isdigit() and 1 <= int(target) <= len(headers):
        return int(target) - 1
    return None


def detect_columns(
    headers: Sequence[str],
    value_columns: Sequence[ValueColumn],
    *,
    overrides: Mapping[str, str] | None = None,
) -> ColumnMap:
    """Work out which column is which. Overrides win; everything else goes by alias.

    An override names a field ('name', 'team', 'positions', or a value field like 'adp') and
    points it at a header or a 1-based column number: `{"adp": "Avg Pick"}`, `{"name": "2"}`.
    """
    overrides = {key.strip().lower(): value for key, value in (overrides or {}).items()}
    headers = tuple(headers)

    def resolve(field_name: str, aliases: Sequence[str]) -> int | None:
        if field_name in overrides:
            index = _resolve_override(headers, overrides[field_name])
            if index is None:
                raise ImportParseError(
                    f"column-map override {field_name}={overrides[field_name]!r} matches no "
                    f"column; headers are {list(headers)}"
                )
            return index
        return _match_alias(headers, aliases)

    name = resolve(NAME_FIELD, NAME_ALIASES)
    if name is None:
        raise ImportParseError(
            f"no player-name column found in {list(headers)}. Expected one of "
            f"{list(NAME_ALIASES)}, or pass a column map like {{'name': 'Player'}}."
        )

    values: dict[str, int] = {}
    for column in value_columns:
        index = resolve(column.field, column.aliases)
        if index is not None:
            values[column.field] = index

    missing = [
        column.field for column in value_columns if column.required and column.field not in values
    ]
    if missing:
        raise ImportParseError(
            f"no column found for required value(s) {missing} in {list(headers)}. Expected one "
            f"of "
            + ", ".join(
                f"{column.field}: {list(column.aliases)}"
                for column in value_columns
                if column.field in missing
            )
            + ", or pass a column map."
        )

    return ColumnMap(
        headers=headers,
        name=name,
        team=resolve(TEAM_FIELD, TEAM_ALIASES),
        positions=resolve(POSITIONS_FIELD, POSITION_ALIASES),
        values=values,
    )


def _cell(row: Sequence[str], index: int | None) -> str:
    """One cell, trimmed, or "" for a short row — ragged rows are normal in exports."""
    if index is None or index >= len(row):
        return ""
    return (row[index] or "").strip()


def parse_table(
    text: str,
    value_columns: Sequence[ValueColumn],
    *,
    overrides: Mapping[str, str] | None = None,
    delimiter: str | None = None,
) -> ParsedTable:
    """Parse a pasted or uploaded table into rows. Writes nothing, matches nothing.

    The first non-blank line is the header. Blank lines, and rows whose name cell is empty
    (trailing "Total" rows, separator rows), are counted as skipped rather than failing the
    import — a source that appends a footer shouldn't cost us the other 200 rows.
    """
    if not (text or "").strip():
        raise ImportParseError("nothing to import: the CSV/paste was empty")

    delimiter = delimiter or sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, skipinitialspace=True)

    columns: ColumnMap | None = None
    rows: list[ParsedRow] = []
    skipped = 0
    text_fields = {column.field for column in value_columns if column.parse_value == PARSE_TEXT}

    for line, raw in enumerate(reader, start=1):
        if not any((cell or "").strip() for cell in raw):
            skipped += 1
            continue
        if columns is None:
            columns = detect_columns(raw, value_columns, overrides=overrides)
            continue

        name = _cell(raw, columns.name)
        if not name:
            skipped += 1
            continue

        rows.append(
            ParsedRow(
                line=line,
                name=name,
                team=_cell(raw, columns.team) or None,
                positions=split_positions(_cell(raw, columns.positions)),
                values={
                    field_name: (
                        parse_text(_cell(raw, index))
                        if field_name in text_fields
                        else parse_number(_cell(raw, index))
                    )
                    for field_name, index in columns.values.items()
                },
                index=len(rows) + 1,
            )
        )

    if columns is None:  # pragma: no cover - the empty-text guard above catches this first
        raise ImportParseError("nothing to import: no header row found")

    return ParsedTable(
        columns=columns, rows=tuple(rows), delimiter=delimiter, skipped_blank=skipped
    )
