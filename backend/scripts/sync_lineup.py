"""`make sync-lineup`: pull our team's ESPN lineup, day by day, into the database.

    uv run python -m scripts.sync_lineup [--first 1] [--last 13]

Default range: today's scoring period through the end of the next matchup. OVERWRITES the
stored lineup for every day it covers, manual edits included; `make sync` never touches lineups.
"""

import argparse
import sys

from espn_api.basketball.constant import POSITION_MAP
from sqlalchemy import select

from app.db.models import LineupAssignment, Player
from app.db.session import SessionLocal
from app.espn.client import ESPNClient, ESPNCredentialsError, ESPNRequestError
from app.espn.lineups import pull_lineups
from app.team.calendar import period_date
from app.team.store import my_team


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=int, default=None, help="first scoring period")
    parser.add_argument("--last", type=int, default=None, help="last scoring period")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        client = ESPNClient.from_settings()
        team = my_team(db, client.season)
        if team is None:
            print("No team is marked as ours yet: run `make sync` first.", file=sys.stderr)
            return 1
        pulled = pull_lineups(db, client, team.team_id, args.first, args.last)

        print(
            f"Team {team.team_id} ({team.name}): stored {pulled.assignments} assignments over "
            f"{pulled.days} days, periods {pulled.first_sp}-{pulled.last_sp} "
            f"({period_date(client.season, pulled.first_sp)} to "
            f"{period_date(client.season, pulled.last_sp)})"
        )
        rows = db.execute(
            select(LineupAssignment, Player.full_name)
            .join(Player, Player.espn_player_id == LineupAssignment.player_id)
            .where(
                LineupAssignment.season == client.season,
                LineupAssignment.team_id == team.team_id,
                LineupAssignment.scoring_period_id.in_({pulled.first_sp, pulled.last_sp}),
            )
            .order_by(
                LineupAssignment.scoring_period_id,
                LineupAssignment.lineup_slot_id,
                Player.full_name,
            )
        ).all()
        day = None
        for row, name in rows:
            if row.scoring_period_id != day:
                day = row.scoring_period_id
                print(f"  period {day} ({period_date(client.season, day)}):")
            print(f"    {POSITION_MAP.get(row.lineup_slot_id, row.lineup_slot_id):<3} {name}")
    except ESPNCredentialsError as exc:
        print(f"ESPN credentials problem: {exc}", file=sys.stderr)
        return 2
    except ESPNRequestError as exc:
        print(f"ESPN lineup pull failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
