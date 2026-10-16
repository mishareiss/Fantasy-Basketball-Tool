"""Waiver-plan auto-detect: planned moves marked done from ESPN's transaction log.

The recorded league (team 8 is ours) plus the hand-seeded pool from the Team API tests;
transactions are added per test so each expectation reads off its own seed.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import EspnTransaction, PlanCandidate, PlanMove
from app.espn.sync import sync_league
from app.team.plan_detect import detect_plan_moves
from tests.conftest import MY_TEAM_ID, PRESEASON_NOW, SEASON
from tests.test_api_team_views import (  # noqa: F401  (fixtures)
    ALPHA,
    BRAVO,
    DELTA,
    GOBERT,
    HALIBURTON,
    SIMONS,
    api,
    client_stub,
    league,
    pool,
)

LATER = PRESEASON_NOW + timedelta(hours=1)


def _move(
    db,
    sp: int,
    candidates: list[int],
    *,
    drop: int | None = None,
    drop_from: int | None = None,
    status: str = "planned",
    created: datetime = PRESEASON_NOW,
    team: int = MY_TEAM_ID,
) -> PlanMove:
    row = PlanMove(
        season=SEASON,
        team_id=team,
        scoring_period_id=sp,
        sequence=0,
        drop_player_id=drop,
        drop_from_move_id=drop_from,
        status=status,
        drop_mismatch=False,
        created_at=created,
        updated_at=created,
    )
    row.candidates = [PlanCandidate(rank=i, player_id=p) for i, p in enumerate(candidates, 1)]
    db.add(row)
    db.commit()
    return row


def _tx(
    db,
    tx_id: str,
    adds: list[int],
    drops: list[int] = (),
    *,
    team: int = MY_TEAM_ID,
    status: str = "EXECUTED",
    kind: str = "FREEAGENT",
    when: datetime = LATER,
) -> None:
    items = [{"type": "ADD", "playerId": p, "fromTeamId": 0, "toTeamId": team} for p in adds]
    items += [{"type": "DROP", "playerId": p, "fromTeamId": team, "toTeamId": 0} for p in drops]
    db.add(
        EspnTransaction(
            id=tx_id,
            season=SEASON,
            type=kind,
            status=status,
            team_id=team,
            scoring_period_id=1,
            proposed_at=when,
            processed_at=when if kind == "WAIVER" else None,
            items=items,
        )
    )
    db.commit()


def _detect(db):
    result = detect_plan_moves(db, SEASON, MY_TEAM_ID)
    db.commit()
    return result


def _state(row: PlanMove) -> tuple:
    return (row.status, row.resolved_player_id, row.espn_transaction_id, row.drop_mismatch)


def test_a_matching_add_and_drop_marks_the_move_done(pool, db):
    move = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT)
    _tx(db, "tx-1", [ALPHA], [GOBERT])

    result = _detect(db)

    assert (result.detected, result.different_drop) == (1, 0)
    assert _state(move) == ("done", ALPHA, "tx-1", False)


def test_the_backup_added_resolves_to_him_and_the_chain_follows(pool, db):
    move1 = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT)
    move2 = _move(db, 4, [DELTA], drop_from=move1.id)
    _tx(db, "tx-1", [BRAVO], [GOBERT])

    _detect(db)

    assert _state(move1) == ("done", BRAVO, "tx-1", False)
    assert move2.status == "planned"
    body = pool.get("/team/plan").json()
    chained = next(m for m in body["moves"] if m["id"] == move2.id)
    assert chained["drop"]["player"]["id"] == BRAVO
    assert chained["drop"]["display"] == "Bravo Warrior"


def test_a_different_drop_is_done_with_an_info_until_the_move_is_edited(pool, db):
    move = _move(db, 2, [ALPHA], drop=GOBERT)
    _tx(db, "tx-1", [ALPHA], [HALIBURTON])

    result = _detect(db)

    assert (result.detected, result.different_drop) == (1, 1)
    assert _state(move) == ("done", ALPHA, "tx-1", True)
    assert move.detected_drop_player_id == HALIBURTON
    infos = [w for w in pool.get("/team/plan").json()["warnings"] if w["code"] == "different_drop"]
    assert [(w["severity"], w["message"]) for w in infos] == [
        (
            "info",
            "The Wed 10/21 move (Rudy Gobert → Alpha Laker) was made on ESPN, but you dropped "
            "Tyrese Haliburton, not Rudy Gobert",
        )
    ]

    assert pool.patch(f"/team/plan/moves/{move.id}", json={"note": "ok"}).status_code == 200
    codes = {w["code"] for w in pool.get("/team/plan").json()["warnings"]}
    assert "different_drop" not in codes
    assert move.status == "done"


def test_a_transaction_that_also_drops_the_planned_drop_is_preferred(pool, db):
    move = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT)
    _tx(db, "tx-early", [BRAVO], [HALIBURTON], when=LATER)
    _tx(db, "tx-late", [ALPHA], [GOBERT], when=LATER + timedelta(hours=2))

    _detect(db)
    assert _state(move) == ("done", ALPHA, "tx-late", False)


def test_several_matches_take_the_earliest(pool, db):
    move = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT)
    _tx(db, "tx-b", [BRAVO], [GOBERT], when=LATER + timedelta(hours=2))
    _tx(db, "tx-a", [ALPHA], [GOBERT], when=LATER)

    _detect(db)
    assert _state(move) == ("done", ALPHA, "tx-a", False)


def test_a_pending_claim_flags_the_move_without_changing_it(pool, db):
    move = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT)
    _tx(db, "tx-claim", [BRAVO], [GOBERT], status="PENDING", kind="WAIVER")

    result = _detect(db)

    assert (result.detected, result.claims_pending) == (0, 1)
    assert move.status == "planned"
    body = pool.get("/team/plan").json()
    assert body["moves"][0]["claim_pending"] is True
    day = pool.get("/team/day", params={"sp": 2, "view": "plan"}).json()
    flagged = [r["player"] for r in day["slots"] if r["player"] and r["player"]["id"] == ALPHA]
    assert flagged[0]["plan"]["claim_pending"] is True


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_a_manual_status_is_never_overwritten(pool, db, status):
    move = _move(db, 2, [ALPHA, BRAVO], drop=GOBERT, status=status)
    _tx(db, "tx-1", [ALPHA], [GOBERT])

    assert _detect(db).detected == 0
    assert _state(move) == (status, None, None, False)


def test_a_second_run_changes_nothing_and_a_transaction_is_credited_once(pool, db):
    first = _move(db, 2, [ALPHA], drop=GOBERT)
    second = _move(db, 3, [ALPHA], drop=HALIBURTON)
    _tx(db, "tx-1", [ALPHA], [GOBERT])

    assert _detect(db).detected == 1
    assert _detect(db).detected == 0
    assert _state(first) == ("done", ALPHA, "tx-1", False)
    assert _state(second) == ("planned", None, None, False)


def test_another_teams_transaction_is_ignored(pool, db):
    move = _move(db, 2, [ALPHA], drop=GOBERT)
    _tx(db, "tx-theirs", [ALPHA], team=3)
    other = _move(db, 2, [BRAVO], drop=GOBERT, team=3)
    _tx(db, "tx-theirs-2", [BRAVO], team=3)

    assert _detect(db).detected == 0
    assert move.status == "planned"
    assert other.status == "planned"  # never touches another team's plan either


def test_a_transaction_from_before_the_move_was_planned_is_not_it(pool, db):
    move = _move(db, 2, [ALPHA], drop=GOBERT)
    _tx(db, "tx-old", [ALPHA], [GOBERT], when=PRESEASON_NOW - timedelta(days=1, minutes=1))
    assert _detect(db).detected == 0
    _tx(db, "tx-recent", [ALPHA], [GOBERT], when=PRESEASON_NOW - timedelta(hours=23))
    assert _detect(db).detected == 1
    assert move.espn_transaction_id == "tx-recent"


def test_the_league_sync_runs_detect_against_the_recorded_log(league, db, client_stub):
    """The recorded log has team 8's executed waiver claim 77a11752: ADD Simons, DROP player
    4433192, processed 2026-10-12 07:12Z. A move planned the day before, with Simons as the
    BACKUP and Gobert as the drop, resolves to Simons and flags the different drop. A second
    move with Simons finds that claim taken, but the recorded PENDING claim b5c136b9 (also
    Simons) flags it as pending."""
    created = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    move = _move(db, 2, [ALPHA, SIMONS], drop=GOBERT, created=created)
    other = _move(db, 3, [SIMONS], drop=HALIBURTON, created=created)
    assert _state(move) == ("planned", None, None, False)

    summary = sync_league(db, client_stub, now=PRESEASON_NOW)

    db.refresh(move)
    assert (summary.plan_moves_detected, summary.plan_moves_different_drop) == (1, 1)
    assert summary.plan_claims_pending == 1
    tx_id = db.scalar(select(EspnTransaction.id).where(EspnTransaction.id.like("77a11752%")))
    assert _state(move) == ("done", SIMONS, tx_id, True)
    assert move.detected_drop_player_id == 4433192
    assert other.status == "planned"

    # And again: idempotent.
    assert sync_league(db, client_stub, now=PRESEASON_NOW).plan_moves_detected == 0


def test_an_after_tipoff_move_made_wednesday_evening_pacific_is_detected(pool, db):
    """Wed 10/21's move is made at 7:45 PM PT, which is already Thu 02:45 in UTC (and Thu in
    ESPN's Eastern day). Planned that morning, or written down an hour after making it, it
    still matches: the window is about when the move was created, not the UTC date."""
    morning = datetime(2026, 10, 21, 16, 0, tzinfo=UTC)  # 9:00 AM PT
    evening = datetime(2026, 10, 22, 2, 45, tzinfo=UTC)  # 7:45 PM PT Wed
    planned = _move(db, 2, [ALPHA], drop=GOBERT, created=morning)
    planned.timing = "after_tipoff"
    late = _move(db, 2, [BRAVO], drop=SIMONS, created=evening + timedelta(hours=1))
    late.timing = "after_tipoff"
    db.commit()
    _tx(db, "tx-eve-1", [ALPHA], [GOBERT], when=evening)
    _tx(db, "tx-eve-2", [BRAVO], [SIMONS], when=evening + timedelta(minutes=5))

    assert _detect(db).detected == 2
    assert _state(planned) == ("done", ALPHA, "tx-eve-1", False)
    assert _state(late) == ("done", BRAVO, "tx-eve-2", False)
