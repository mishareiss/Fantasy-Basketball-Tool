"""`make sync-ages`: match nba.com's roster to our players and fill in birthdates and ages.

    uv run python -m scripts.sync_ages [--limit 200] [--refresh] [--delay 0.7] [--as-of DATE]

The first run is the long one: it is one HTTP call to nba.com per matched player, deliberately
paced, so budget ten-odd minutes for a full pool. Every run after that fetches only what is
missing, and killing this halfway is safe — progress is committed as it goes.

Prints the manual-alias worklist at the end: our players nba.com resolved to nothing. Those
are fixed one at a time with `POST /players/{espn_player_id}/aliases`.
"""

import argparse
import sys
from datetime import date

from app.ages import DEFAULT_DELAY, NbaApiError, sync_ages
from app.config import get_settings
from app.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="stop after this many birthdate fetches (best players first); default: no limit",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch birthdates we already have, instead of only the missing ones",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=DEFAULT_DELAY,
        help=f"seconds to wait between nba.com calls (default: {DEFAULT_DELAY})",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=None,
        help="compute ages as of this ISO date instead of the configured AGE_AS_OF",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        summary = sync_ages(
            db, refresh=args.refresh, limit=args.limit, delay=args.delay, as_of=args.as_of
        )
    except NbaApiError as exc:
        print(f"nba.com refused us: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    configured = get_settings().resolved_age_as_of()
    note = "" if summary.age_as_of == configured else f"  (configured default: {configured})"
    print(f"Ages as of {summary.age_as_of}{note}")
    print(
        f"  nba.com roster: {summary.nba_roster} names -> {summary.nba_matched} matched, "
        f"{summary.nba_ambiguous} ambiguous, {summary.nba_unmatched} not in our pool"
    )
    print(f"  aliases: +{summary.aliases_created} new, {summary.aliases_existing} already recorded")
    print(
        f"  birthdates: +{summary.birthdates_fetched} fetched, "
        f"{summary.birthdates_absent} not on file at nba.com, "
        f"{summary.birthdates_failed} failed, {summary.birthdates_pending} still to fetch"
    )
    print(f"  ages: {summary.ages_set} recomputed")
    print(
        f"  coverage: {summary.players_with_age}/{summary.players_total} players have an age "
        f"({summary.players_missing_age} missing, {summary.players_without_alias} of those "
        f"have no nba.com match at all)"
    )

    if summary.ambiguous_names:
        print("\n  ambiguous (two of our players share the name):")
        for line in summary.ambiguous_names:
            print(f"    {line}")

    if summary.unresolved_players:
        print("\n  worklist — our players nba.com matched to nothing (sample):")
        for name in summary.unresolved_players:
            print(f"    {name}")
        print(
            "    POST /players/{espn_player_id}/aliases "
            '{"source": "nba_api", "source_id": "...", "source_name": "..."} to resolve one, '
            "then re-run."
        )

    if summary.birthdates_pending:
        print(f"\n  {summary.birthdates_pending} birthdates still pending — re-run to continue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
