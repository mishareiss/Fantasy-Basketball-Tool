"""`make sync`: pull the league from ESPN into the database and print what changed.

Talks to the same `sync_league()` the API endpoint uses, so it works with the server down.
"""

import sys

from app.db.session import SessionLocal
from app.espn import sync_league
from app.espn.client import ESPNCredentialsError, ESPNRequestError
from app.scoring.settings import ESPNSettingsError
from app.scoring.stats import stat_label


def main() -> int:
    db = SessionLocal()
    try:
        summary = sync_league(db)
    except ESPNCredentialsError as exc:
        print(f"ESPN credentials problem: {exc}", file=sys.stderr)
        return 2
    except (ESPNRequestError, ESPNSettingsError) as exc:
        print(f"ESPN sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(f"League {summary.league_id} ({summary.league_name}) season {summary.season}")
    print(f"  scoring type: {summary.scoring_type}   teams: {summary.team_count}")
    print(f"  roster slots: {summary.roster_slots}")
    print(
        f"  scoring rules: {summary.scoring_rules} "
        f"(+{summary.scoring_rules_created} new, ~{summary.scoring_rules_updated} changed, "
        f"-{summary.scoring_rules_removed} removed)"
    )
    for name, points in sorted(summary.points_by_stat.items(), key=lambda kv: -abs(kv[1])):
        print(f"    {name:<6} {points:>8.2f}   {stat_label(name)}")
    print(
        f"  players: {summary.players_seen} seen "
        f"(+{summary.players_created} new, ~{summary.players_updated} changed, "
        f"={summary.players_unchanged} unchanged)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
