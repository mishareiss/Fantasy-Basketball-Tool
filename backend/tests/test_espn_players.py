"""Parsing ESPN's player pool into canonical player records."""

from app.espn.players import parse_player_entry, parse_player_pool

# ESPN's own shape for a rostered player. The recorded fixture is all free agents (our league
# is a dynasty startup that hasn't drafted), so roster status gets its own case here.
ROSTERED_ENTRY = {
    "id": 3945274,
    "status": "ONTEAM",
    "onTeamId": 4,
    "player": {
        "id": 3945274,
        "fullName": "Luka Doncic",
        "firstName": "Luka",
        "lastName": "Doncic",
        "defaultPositionId": 1,
        "eligibleSlots": [0, 1, 5, 8, 11, 12, 13],
        "proTeamId": 13,
        "injuryStatus": "DAY_TO_DAY",
        "injured": True,
    },
}


def test_parses_the_recorded_pool(player_pool_payload):
    records = parse_player_pool(player_pool_payload)

    assert len(records) == len(player_pool_payload)
    assert all(record.espn_player_id > 0 for record in records)
    assert all(record.full_name for record in records)


def test_maps_team_and_positions(player_pool_payload):
    by_name = {record.full_name: record for record in parse_player_pool(player_pool_payload)}
    sga = by_name["Shai Gilgeous-Alexander"]

    assert sga.espn_player_id == 4278073
    assert sga.nba_team == "OKC"
    assert sga.primary_position == "PG"
    # Only real positions: ESPN's G / G/F / UT / BE / IR slots are lineup constructs.
    assert sga.positions == ["PG"]


def test_positions_keep_pg_to_c_order():
    record = parse_player_entry(ROSTERED_ENTRY)

    assert record is not None
    assert record.positions == ["PG", "SG"]
    assert record.nba_team == "LAL"


def test_reads_roster_status_and_injury():
    record = parse_player_entry(ROSTERED_ENTRY)

    assert record is not None
    assert record.roster_status == "ONTEAM"
    assert record.espn_fantasy_team_id == 4
    assert record.injury_status == "DAY_TO_DAY"
    assert record.injured is True


def test_free_agents_have_no_fantasy_team(player_pool_payload):
    records = parse_player_pool(player_pool_payload)
    free_agents = [record for record in records if record.roster_status == "FREEAGENT"]

    assert free_agents  # the recorded league hasn't drafted yet
    assert all(record.espn_fantasy_team_id is None for record in free_agents)


def test_age_is_absent_because_espn_does_not_publish_it(player_pool_payload):
    """Documents the gap: the authoritative age source arrives with the dynasty curve."""
    records = parse_player_pool(player_pool_payload)

    assert all(record.birthdate is None and record.age is None for record in records)


def test_parses_a_birthdate_if_espn_ever_returns_one():
    entry = {**ROSTERED_ENTRY, "player": {**ROSTERED_ENTRY["player"], "dateOfBirth": "1999-02-28"}}

    record = parse_player_entry(entry)

    assert record is not None
    assert record.birthdate is not None
    assert record.birthdate.year == 1999
    assert record.age is not None and record.age > 20


def test_skips_entries_without_a_usable_player():
    assert parse_player_entry({"id": 1, "status": "FREEAGENT"}) is None
    assert parse_player_entry({"player": {"id": 5}}) is None  # no name


def test_deduplicates_players_across_pages(player_pool_payload):
    doubled = player_pool_payload + player_pool_payload

    assert len(parse_player_pool(doubled)) == len(parse_player_pool(player_pool_payload))
