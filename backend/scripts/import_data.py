"""`make import`: bring a CSV/paste of external data in, matched to our players.

    uv run python -m scripts.import_data --kind adp --source hashtag --season 2027 \
        --file ~/Downloads/adp.csv [--commit] [--map name=PLAYER,adp="Avg Pick"] [--strict]

Dry run by default: it parses, matches, and prints exactly what it *would* write, including
the review and unmatched worklists. Nothing is stored until `--commit`.

Committing twice is a no-op — the second run reports every row unchanged and creates no
aliases, which is the cheap way to confirm a file has already landed.

Read `-` (or leave `--file` off) to take the table on stdin, so a spreadsheet paste works:

    pbpaste | uv run python -m scripts.import_data --kind adp --source hashtag --commit
"""

import argparse
import sys

from app.config import get_settings
from app.db.session import SessionLocal
from app.ingest import (
    STATUS_DUPLICATE,
    STATUS_INVALID,
    STATUS_MATCHED,
    STATUS_REVIEW,
    STATUS_UNMATCHED,
    ImportParseError,
    ImportSummary,
    UnknownKindError,
    accept_only_certain,
    kind_names,
    run_import,
)
from app.ingest.registry import PLANNED_KINDS

# How many rows of each kind to print. The worklists are meant to be worked through, so they
# get more room than the matched rows, which are only there to eyeball a few for sanity.
SAMPLE_MATCHED = 10
SAMPLE_WORKLIST = 25


def parse_column_map(raw: str | None) -> dict[str, str] | None:
    """`--map name=PLAYER,adp=3` -> {'name': 'PLAYER', 'adp': '3'}."""
    if not raw:
        return None
    mapping: dict[str, str] = {}
    for pair in raw.split(","):
        field, separator, header = pair.partition("=")
        if not separator or not field.strip() or not header.strip():
            raise SystemExit(f"--map expects field=header pairs, got {pair!r}")
        mapping[field.strip()] = header.strip()
    return mapping


def _print_rows(label: str, rows, limit: int, *, show_candidates: bool) -> None:
    if not rows:
        return
    print(f"\n  {label} ({len(rows)}):")
    for row in rows[:limit]:
        values = " ".join(
            f"{field}={value:g}" for field, value in row.values.items() if value is not None
        )
        target = f" -> {row.player_name}" if row.player_name else ""
        confidence = f" [{row.method} {row.confidence:.2f}]" if row.method else ""
        note = f"  ({row.note})" if row.note else ""
        print(f"    line {row.line:>4}  {row.source_name:<26}{target}{confidence}  {values}{note}")
        if show_candidates and row.candidates:
            for candidate in row.candidates:
                print(
                    f"          candidate: {candidate['full_name']} "
                    f"({candidate['nba_team']}) id={candidate['player_id']} "
                    f"score={candidate['score']:.2f}"
                )
    if len(rows) > limit:
        print(f"    ... and {len(rows) - limit} more")


def report(summary: ImportSummary) -> None:
    """Print the preview or the receipt. Same shape either way."""
    mode = "DRY RUN (nothing written)" if summary.dry_run else "COMMITTED"
    print(
        f"{mode}  kind={summary.kind}  source={summary.source}  season={summary.season}  "
        f"delimiter={summary.delimiter!r}"
    )
    print(f"  columns: {summary.columns}")
    print(
        f"  rows: {summary.rows_parsed} parsed"
        + (
            f", {summary.rows_skipped_blank} blank/headerless skipped"
            if summary.rows_skipped_blank
            else ""
        )
    )
    print(
        f"  matched {summary.matched}  review {summary.review}  unmatched {summary.unmatched}"
        f"  duplicate {summary.duplicate}  invalid {summary.invalid}"
    )
    verb = "would create" if summary.dry_run else "created"
    print(
        f"  aliases: {verb} {summary.aliases_created}, {summary.aliases_existing} already recorded"
    )
    print(
        f"  {summary.kind} rows: {verb} {summary.rows_created}, "
        f"{'would update' if summary.dry_run else 'updated'} {summary.rows_updated}, "
        f"{summary.rows_unchanged} unchanged"
    )

    _print_rows("matched", summary.of_status(STATUS_MATCHED), SAMPLE_MATCHED, show_candidates=False)
    _print_rows(
        "review — needs a human",
        summary.of_status(STATUS_REVIEW),
        SAMPLE_WORKLIST,
        show_candidates=True,
    )
    _print_rows(
        "unmatched — no player of ours looks like this",
        summary.of_status(STATUS_UNMATCHED),
        SAMPLE_WORKLIST,
        show_candidates=True,
    )
    _print_rows(
        "duplicate rows",
        summary.of_status(STATUS_DUPLICATE),
        SAMPLE_WORKLIST,
        show_candidates=False,
    )
    _print_rows(
        "invalid — no value in a required column",
        summary.of_status(STATUS_INVALID),
        SAMPLE_WORKLIST,
        show_candidates=False,
    )

    if summary.worklist:
        print(
            "\n  Resolve one with:\n"
            '    POST /players/{espn_player_id}/aliases {"source": "'
            + summary.source
            + '", "source_name": "<the name above>"}\n'
            "  ...then re-import: the alias resolves it instantly and identically."
        )
    if summary.dry_run:
        print("\n  Nothing was written. Re-run with --commit to store this.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--kind",
        required=True,
        help=f"what the data is. built: {', '.join(kind_names())}; "
        f"planned: {', '.join(sorted(PLANNED_KINDS))}",
    )
    parser.add_argument("--source", required=True, help="who published it: hashtag, fantasypros")
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="season the data is FOR (defaults to ESPN_SEASON)",
    )
    parser.add_argument("--file", default=None, help="CSV/TSV file; '-' or omitted reads stdin")
    parser.add_argument(
        "--map",
        dest="column_map",
        default=None,
        help='override column detection: --map name=PLAYER,adp="Avg Pick" (or a 1-based number)',
    )
    parser.add_argument("--delimiter", default=None, help="force a delimiter instead of sniffing")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="hold fuzzy matches for confirmation instead of auto-accepting them",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="actually write. without it this is a preview and nothing is stored",
    )
    args = parser.parse_args()

    season = args.season if args.season is not None else get_settings().espn_season
    if season is None:
        print(
            "No season: pass --season, or set ESPN_SEASON. A stored row without a season can't "
            "be compared to anything later.",
            file=sys.stderr,
        )
        return 2

    if args.file in (None, "-"):
        text = sys.stdin.read()
    else:
        # utf-8-sig: exports out of Excel carry a BOM, which would otherwise glue itself to the
        # first header and stop it matching any alias.
        with open(args.file, encoding="utf-8-sig") as handle:
            text = handle.read()

    db = SessionLocal()
    try:
        summary = run_import(
            db,
            kind=args.kind,
            source=args.source,
            season=season,
            text=text,
            column_map=parse_column_map(args.column_map),
            delimiter=args.delimiter,
            dry_run=not args.commit,
            accept=accept_only_certain if args.strict else None,
        )
    except UnknownKindError as exc:
        print(exc.args[0], file=sys.stderr)
        return 2
    except ImportParseError as exc:
        print(f"Can't read that table: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    report(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
