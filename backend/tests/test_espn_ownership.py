"""Parsing ESPN's ownership block into ADP records."""

from app.espn.ownership import parse_ownership, parse_ownership_entry


def _entry(ownership: dict | None, player_id: int = 1) -> dict:
    player: dict = {"id": player_id, "fullName": "Test Player"}
    if ownership is not None:
        player["ownership"] = ownership
    return {"id": player_id, "player": player}


def test_parses_adp_auction_value_and_roster_share():
    record = parse_ownership_entry(
        _entry({"averageDraftPosition": 3.1, "auctionValueAverage": 63.66, "percentOwned": 99.91})
    )

    assert record is not None
    assert (record.adp, record.auction_value, record.percent_owned) == (3.1, 63.66, 99.91)


def test_values_are_stored_exactly_as_espn_sends_them():
    """ESPN floors ADP at the last pick of a standard draft; that sentinel is information."""
    record = parse_ownership_entry(
        _entry({"averageDraftPosition": 140.0, "auctionValueAverage": 0.0, "percentOwned": 0.0})
    )

    assert record is not None
    assert record.adp == 140.0
    assert record.auction_value == 0.0


def test_missing_numbers_are_none_not_zero():
    record = parse_ownership_entry(_entry({"percentOwned": 12.5}))

    assert record is not None
    assert record.adp is None and record.auction_value is None
    assert record.percent_owned == 12.5


def test_no_ownership_block_yields_no_record():
    assert parse_ownership_entry(_entry(None)) is None
    assert parse_ownership_entry({"id": 1}) is None


def test_parses_the_recorded_pool(player_pool_payload):
    records = parse_ownership(player_pool_payload)

    assert len(records) == len(player_pool_payload)
    assert all(record.adp is not None for record in records)


def test_deduplicates_across_pages(player_pool_payload):
    doubled = player_pool_payload + player_pool_payload

    assert len(parse_ownership(doubled)) == len(player_pool_payload)
