"""Which slot each rostered player is in on a given day. Pure: no database, no ESPN."""

from collections.abc import Mapping

# ESPN lineup slot ids (espn_api POSITION_MAP) the resolver itself needs.
BENCH_SLOT = 12
IR_SLOT = 13


def resolve_lineup(
    stored_rows_by_day: Mapping[int, Mapping[int, int]],
    roster_slots: Mapping[int, int],
    sp: int,
) -> dict[int, int]:
    """`{player_id: slot_id}` for day `sp`.

    `stored_rows_by_day` is the stored `LineupAssignment`s, `{scoring_period: {player: slot}}`.
    `roster_slots` is the current roster, `{player: RosterEntry.lineup_slot_id}`. Its keys are
    who is on the team.

    The lineup is day `sp`'s stored rows if it has any, else the nearest EARLIER day that has
    rows (a lineup carries forward until someone changes it), else the slots the roster sync
    saw. That is then reconciled with the roster: anyone no longer on it is dropped, and anyone
    on it with no assignment (picked up since the lineup was set) goes to the bench.
    """
    earlier = [day for day, rows in stored_rows_by_day.items() if day <= sp and rows]
    base: Mapping[int, int] = stored_rows_by_day[max(earlier)] if earlier else roster_slots
    return {player_id: base.get(player_id, BENCH_SLOT) for player_id in roster_slots}
