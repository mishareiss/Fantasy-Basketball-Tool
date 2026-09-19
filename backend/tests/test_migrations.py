"""Migrations driven for real against a throwaway SQLite file.

Worth a test rather than an eyeball, because the interesting half of that migration is *data*:
1,000-odd existing ADP rows have to come out the other side with the right season on them
before NOT NULL is enforced. A schema-only check would pass whether or not the backfill worked.

The migration is written with `batch_alter_table` so it runs on SQLite as well as Postgres,
which is what lets this run offline in `make test`. Postgres is still where it matters, and
`make migrate` is still the acceptance check.
"""

from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app.config import get_settings
from tests.conftest import SEASON

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# The revision that added `adp_entry` without a season, and the one under test.
BEFORE = "b46374371451"
UNDER_TEST = "637150ee8d91"

# The ranking tables, and the revision they arrived in.
RANKING_BEFORE = UNDER_TEST
RANKING = "b9e3dada060f"

# The revision that put `horizon` on `ranking_set` and re-keyed the table with it.
HORIZON = "c41d9a7e5b30"


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    """A SQLite database at the revision *before* the season migration, plus a runner."""
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(get_settings(), "database_url", database_url)

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))

    engine = create_engine(database_url, future=True)
    command.upgrade(config, BEFORE)
    try:
        yield config, engine
    finally:
        engine.dispose()


def _seed(engine, rows: list[tuple[int, str, float]], *, season: int | None = None) -> None:
    """Insert players and ADP rows the way the pre-migration schema holds them."""
    column = ", season" if season is not None else ""
    value = f", {season}" if season is not None else ""
    with engine.begin() as connection:
        for player_id, *_ in rows:
            connection.execute(
                text(
                    "INSERT OR IGNORE INTO player "
                    "(espn_player_id, full_name, positions, injured, created_at, updated_at) "
                    f"VALUES ({player_id}, 'Player {player_id}', '[]', 0, '2026-01-01', "
                    "'2026-01-01')"
                )
            )
        for player_id, source, adp in rows:
            connection.execute(
                text(
                    f"INSERT INTO adp_entry (player_id, source, adp, as_of{column}) "
                    f"VALUES ({player_id}, '{source}', {adp}, '2026-08-01'{value})"
                )
            )


def _adp_rows(engine) -> list[tuple]:
    with engine.begin() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT player_id, source, season, adp FROM adp_entry "
                    "ORDER BY player_id, season"
                )
            )
        )


def test_existing_adp_is_backfilled_with_the_configured_season(migrated):
    """Every row today is ESPN's read on the one season we sync, so that's the answer."""
    config, engine = migrated
    _seed(engine, [(1, "espn", 5.0), (2, "espn", 9.0), (3, "espn", 140.0)])

    command.upgrade(config, UNDER_TEST)

    assert _adp_rows(engine) == [
        (1, "espn", SEASON, 5.0),
        (2, "espn", SEASON, 9.0),
        (3, "espn", SEASON, 140.0),
    ]


def test_the_new_key_lets_two_seasons_of_one_player_coexist(migrated):
    config, engine = migrated
    _seed(engine, [(1, "espn", 5.0)])
    command.upgrade(config, UNDER_TEST)

    _seed(engine, [(1, "espn", 42.0)], season=SEASON - 1)

    assert _adp_rows(engine) == [(1, "espn", SEASON - 1, 42.0), (1, "espn", SEASON, 5.0)]
    unique = {
        constraint["name"] for constraint in inspect(engine).get_unique_constraints("adp_entry")
    }
    assert unique == {"uq_adp_entry_player_source_season"}


def test_the_migration_refuses_to_guess_a_season_it_was_not_given(migrated, monkeypatch):
    """Better a failed migration than 1,000 ADP rows stamped with the wrong year."""
    config, engine = migrated
    monkeypatch.setattr(get_settings(), "espn_season", None)
    _seed(engine, [(1, "espn", 5.0)])

    with pytest.raises(RuntimeError, match="ESPN_SEASON"):
        command.upgrade(config, UNDER_TEST)


def test_an_empty_table_needs_no_season_to_migrate(migrated, monkeypatch):
    config, engine = migrated
    monkeypatch.setattr(get_settings(), "espn_season", None)

    command.upgrade(config, UNDER_TEST)

    assert _adp_rows(engine) == []


def test_the_downgrade_keeps_the_newest_season_of_each_player(migrated):
    """Lossy on purpose: the old key has nowhere to put a second season."""
    config, engine = migrated
    _seed(engine, [(1, "espn", 5.0)])
    command.upgrade(config, UNDER_TEST)
    _seed(engine, [(1, "espn", 42.0)], season=SEASON - 1)
    _seed(engine, [(2, "hashtag", 3.0)], season=SEASON)

    command.downgrade(config, BEFORE)

    with engine.begin() as connection:
        rows = list(
            connection.execute(
                text("SELECT player_id, source, adp FROM adp_entry ORDER BY player_id, source")
            )
        )
    assert rows == [(1, "espn", 5.0), (2, "hashtag", 3.0)]
    assert "season" not in {column["name"] for column in inspect(engine).get_columns("adp_entry")}


def test_upgrade_downgrade_upgrade_leaves_a_working_schema(migrated):
    config, engine = migrated
    _seed(engine, [(1, "espn", 5.0)])

    command.upgrade(config, UNDER_TEST)
    command.downgrade(config, BEFORE)
    command.upgrade(config, UNDER_TEST)

    assert _adp_rows(engine) == [(1, "espn", SEASON, 5.0)]


def test_a_from_scratch_apply_reaches_head(migrated):
    """The other half of "the migration applies": a cold database, all the way up."""
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    columns = {column["name"] for column in inspect(engine).get_columns("adp_entry")}
    assert "season" in columns


# --- ranking_set / ranking_entry ------------------------------------------------------------


def _table_names(engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_the_ranking_tables_arrive_together_and_keyed(migrated):
    config, engine = migrated

    command.upgrade(config, RANKING)

    assert {"ranking_set", "ranking_entry"} <= _table_names(engine)
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_unique_constraints("ranking_set")} == {
        "uq_ranking_set_source_name_season"
    }
    assert {c["name"] for c in inspector.get_unique_constraints("ranking_entry")} == {
        "uq_ranking_entry_set_player"
    }
    assert {index["name"] for index in inspector.get_indexes("ranking_entry")} == {
        "ix_ranking_entry_player_id",
        "ix_ranking_entry_ranking_set_id",
    }


def test_a_set_cannot_hold_one_player_twice(migrated):
    """The constraint the wholesale replace leans on: no stale duplicate can survive it."""
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("INSERT INTO ranking_entry (ranking_set_id, player_id, rank) VALUES (1, 1, 99)")
        )


def test_dropping_a_set_takes_its_entries_with_it(migrated):
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("DELETE FROM ranking_set WHERE id = 1"))
        remaining = connection.scalar(text("SELECT count(*) FROM ranking_entry"))

    assert remaining == 0


def test_the_ranking_downgrade_removes_both_tables(migrated):
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    command.downgrade(config, RANKING_BEFORE)

    assert not {"ranking_set", "ranking_entry"} & _table_names(engine)


def test_a_from_scratch_apply_reaches_the_ranking_tables(migrated):
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert {"ranking_set", "ranking_entry"} <= _table_names(engine)


def _seed_ranking(engine) -> None:
    """One set with two entries, inserted the way the importer would (pre-horizon shape)."""
    with engine.begin() as connection:
        _seed_players(connection, (1, 2))
        connection.execute(
            text(
                "INSERT INTO ranking_set (id, source, name, season, as_of) "
                "VALUES (1, 'hashtag', 'Top 200', 2027, '2026-08-01')"
            )
        )
        for player_id, rank in ((1, 1), (2, 2)):
            connection.execute(
                text(
                    "INSERT INTO ranking_entry (ranking_set_id, player_id, rank, tier) "
                    f"VALUES (1, {player_id}, {rank}, 'Tier 1')"
                )
            )


def _seed_players(connection, player_ids) -> None:
    for player_id in player_ids:
        connection.execute(
            text(
                "INSERT OR IGNORE INTO player "
                "(espn_player_id, full_name, positions, injured, created_at, updated_at) "
                f"VALUES ({player_id}, 'Player {player_id}', '[]', 0, '2026-01-01', "
                "'2026-01-01')"
            )
        )


# --- ranking_set.horizon --------------------------------------------------------------------


def _ranking_sets(engine) -> list[tuple]:
    with engine.begin() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT id, source, name, season, horizon FROM ranking_set "
                    "ORDER BY name, horizon"
                )
            )
        )


def _seed_horizon_set(engine, set_id: int, *, name: str, horizon: str, as_of: str) -> None:
    """One post-migration set, so the new key can be exercised."""
    with engine.begin() as connection:
        _seed_players(connection, (1,))
        connection.execute(
            text(
                "INSERT INTO ranking_set (id, source, name, season, horizon, as_of) VALUES "
                f"({set_id}, 'hashtag', '{name}', 2027, '{horizon}', '{as_of}')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO ranking_entry (ranking_set_id, player_id, rank) "
                f"VALUES ({set_id}, 1, 1)"
            )
        )


def test_an_existing_set_is_backfilled_as_redraft(migrated):
    """Near enough every published rank list is redraft, and a dynasty one re-imports in a call."""
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    command.upgrade(config, HORIZON)

    assert _ranking_sets(engine) == [(1, "hashtag", "Top 200", 2027, "redraft")]


def test_the_new_key_lets_both_horizons_of_one_name_coexist(migrated):
    config, engine = migrated
    command.upgrade(config, HORIZON)

    _seed_horizon_set(engine, 1, name="Top 200", horizon="dynasty", as_of="2026-08-01")
    _seed_horizon_set(engine, 2, name="Top 200", horizon="redraft", as_of="2026-08-02")

    assert [row[4] for row in _ranking_sets(engine)] == ["dynasty", "redraft"]
    assert {
        constraint["name"] for constraint in inspect(engine).get_unique_constraints("ranking_set")
    } == {"uq_ranking_set_source_name_season_horizon"}


def test_the_old_key_is_gone_so_a_second_horizon_is_not_a_conflict(migrated):
    """Belt and braces: the same insert would have been an IntegrityError one revision back."""
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO ranking_set (id, source, name, season, as_of) "
                "VALUES (2, 'hashtag', 'Top 200', 2027, '2026-08-02')"
            )
        )

    command.upgrade(config, HORIZON)
    _seed_horizon_set(engine, 2, name="Top 200", horizon="dynasty", as_of="2026-08-02")

    assert len(_ranking_sets(engine)) == 2


def test_the_horizon_downgrade_keeps_the_newest_set_of_each_name(migrated):
    """Lossy on purpose: the old key has nowhere to put a second horizon."""
    config, engine = migrated
    command.upgrade(config, HORIZON)
    _seed_horizon_set(engine, 1, name="Top 200", horizon="dynasty", as_of="2026-08-01")
    _seed_horizon_set(engine, 2, name="Top 200", horizon="redraft", as_of="2026-08-02")
    _seed_horizon_set(engine, 3, name="Our Board", horizon="dynasty", as_of="2026-08-03")

    command.downgrade(config, RANKING)

    with engine.begin() as connection:
        rows = list(
            connection.execute(text("SELECT id, name FROM ranking_set ORDER BY id")),
        )
        orphans = connection.scalar(
            text(
                "SELECT count(*) FROM ranking_entry WHERE ranking_set_id NOT IN "
                "(SELECT id FROM ranking_set)"
            )
        )
    assert rows == [(2, "Top 200"), (3, "Our Board")]
    # The entries of the set that lost went with it, rather than being left dangling.
    assert orphans == 0
    columns = {column["name"] for column in inspect(engine).get_columns("ranking_set")}
    assert "horizon" not in columns


def test_a_from_scratch_apply_reaches_the_horizon_column(migrated):
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    columns = {column["name"] for column in inspect(engine).get_columns("ranking_set")}
    assert "horizon" in columns


def test_the_horizon_upgrade_downgrade_upgrade_leaves_a_working_schema(migrated):
    config, engine = migrated
    command.upgrade(config, RANKING)
    _seed_ranking(engine)

    command.upgrade(config, HORIZON)
    command.downgrade(config, RANKING)
    command.upgrade(config, HORIZON)

    assert _ranking_sets(engine) == [(1, "hashtag", "Top 200", 2027, "redraft")]


# --- market_line -----------------------------------------------------------------------------

# The revision that added `market_line`, and the one it sits on.
MARKET_BEFORE = HORIZON
MARKET = "d7a4f1c26b38"


def _seed_market_line(engine, *, player_id=1, stat_id=0, line=27.5, source="market", season=2027):
    with engine.begin() as connection:
        _seed_players(connection, (player_id,))
        connection.execute(
            text(
                "INSERT INTO market_line "
                "(player_id, source, season, stat_id, line, over_odds, under_odds, as_of) "
                f"VALUES ({player_id}, '{source}', {season}, {stat_id}, {line}, -110, -110, "
                "'2026-08-01')"
            )
        )


def test_the_market_line_table_arrives_keyed_and_indexed(migrated):
    config, engine = migrated

    command.upgrade(config, MARKET)

    assert "market_line" in _table_names(engine)
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_unique_constraints("market_line")} == {
        "uq_market_line_source_season_player_stat"
    }
    assert {index["name"] for index in inspector.get_indexes("market_line")} == {
        "ix_market_line_player_id"
    }
    columns = inspector.get_columns("market_line")
    nullable = {column["name"]: column["nullable"] for column in columns}
    # A line with no published price is still a line.
    assert nullable["over_odds"] and nullable["under_odds"]
    assert not nullable["line"] and not nullable["season"] and not nullable["stat_id"]


def test_one_stat_cannot_be_priced_twice_by_one_book_in_one_season(migrated):
    """The key that makes "update the odds if they change" an UPDATE rather than a second row."""
    config, engine = migrated
    command.upgrade(config, MARKET)
    _seed_market_line(engine)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO market_line "
                "(player_id, source, season, stat_id, line, as_of) "
                "VALUES (1, 'market', 2027, 0, 28.5, '2026-08-02')"
            )
        )


def test_a_second_book_a_second_season_and_a_second_stat_all_coexist(migrated):
    config, engine = migrated
    command.upgrade(config, MARKET)

    _seed_market_line(engine, stat_id=0)
    _seed_market_line(engine, stat_id=3, line=9.5)
    _seed_market_line(engine, stat_id=0, line=28.5, source="draftkings")
    _seed_market_line(engine, stat_id=0, line=26.5, season=2026)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM market_line")) == 4


def test_dropping_a_player_takes_his_lines_with_him(migrated):
    config, engine = migrated
    command.upgrade(config, MARKET)
    _seed_market_line(engine)

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("DELETE FROM player WHERE espn_player_id = 1"))
        remaining = connection.scalar(text("SELECT count(*) FROM market_line"))

    assert remaining == 0


def test_the_market_line_downgrade_removes_the_table(migrated):
    config, engine = migrated
    command.upgrade(config, MARKET)
    _seed_market_line(engine)

    command.downgrade(config, MARKET_BEFORE)

    assert "market_line" not in _table_names(engine)


def test_the_market_line_upgrade_downgrade_upgrade_leaves_a_working_schema(migrated):
    config, engine = migrated
    command.upgrade(config, MARKET)
    _seed_market_line(engine)

    command.downgrade(config, MARKET_BEFORE)
    command.upgrade(config, MARKET)
    _seed_market_line(engine)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM market_line")) == 1


def test_a_from_scratch_apply_reaches_the_market_line_table(migrated):
    """The whole point of the migration test: a cold database, all the way up, on SQLite."""
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert "market_line" in _table_names(engine)


# --- master_rank_entry -------------------------------------------------------------------------

# The revision that added our own board, and the one it sits on.
MASTER_BEFORE = MARKET
MASTER = "a3f27c91b054"


def _seed_master_entry(engine, *, player_id=1, rank=1, excluded=0, tag=None, note=None):
    """One board row, inserted the way the application does — WITHOUT a timestamp.

    The omission is the point: `updated_at` is a server default, and this is the first table
    whose rows are typed rather than synced, so an INSERT that leaves it out has to work on
    both dialects.
    """
    tag_sql = "NULL" if tag is None else f"'{tag}'"
    note_sql = "NULL" if note is None else f"'{note}'"
    rank_sql = "NULL" if rank is None else str(rank)
    with engine.begin() as connection:
        _seed_players(connection, (player_id,))
        connection.execute(
            text(
                "INSERT INTO master_rank_entry (player_id, rank, excluded, tag, note) "
                f"VALUES ({player_id}, {rank_sql}, {excluded}, {tag_sql}, {note_sql})"
            )
        )


def test_the_master_board_table_arrives_keyed_and_indexed(migrated):
    config, engine = migrated

    command.upgrade(config, MASTER)

    assert "master_rank_entry" in _table_names(engine)
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_unique_constraints("master_rank_entry")} == {
        "uq_master_rank_entry_player"
    }
    assert {index["name"] for index in inspector.get_indexes("master_rank_entry")} == {
        "ix_master_rank_entry_player_id"
    }
    nullable = {
        column["name"]: column["nullable"] for column in inspector.get_columns("master_rank_entry")
    }
    # A player set aside has no place in the order, and a note is optional.
    assert nullable["rank"] and nullable["tag"] and nullable["note"]
    assert not nullable["player_id"] and not nullable["excluded"]


def test_a_row_can_be_written_without_a_timestamp_or_an_excluded_flag(migrated):
    """The defaults are the ones the application actually leans on."""
    config, engine = migrated
    command.upgrade(config, MASTER)

    with engine.begin() as connection:
        _seed_players(connection, (1,))
        connection.execute(text("INSERT INTO master_rank_entry (player_id, rank) VALUES (1, 1)"))
        row = connection.execute(
            text("SELECT excluded, updated_at, tag, note FROM master_rank_entry")
        ).one()

    assert not row[0]
    assert row[1] is not None
    assert row[2] is None and row[3] is None


def test_one_player_cannot_be_on_the_board_twice(migrated):
    """One board: the key that makes "his rank" a question with one answer."""
    config, engine = migrated
    command.upgrade(config, MASTER)
    _seed_master_entry(engine)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text("INSERT INTO master_rank_entry (player_id, rank) VALUES (1, 7)"))


def test_a_set_aside_player_is_stored_with_no_rank_at_all(migrated):
    config, engine = migrated
    command.upgrade(config, MASTER)

    _seed_master_entry(engine, player_id=1, rank=None, excluded=1, tag="fade", note="the knee")

    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT rank, excluded, tag, note FROM master_rank_entry")
        ).one()
    assert row[0] is None and row[1] and row[2] == "fade" and row[3] == "the knee"


def test_dropping_a_player_takes_his_board_row_with_him(migrated):
    config, engine = migrated
    command.upgrade(config, MASTER)
    _seed_master_entry(engine)

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("DELETE FROM player WHERE espn_player_id = 1"))
        remaining = connection.scalar(text("SELECT count(*) FROM master_rank_entry"))

    assert remaining == 0


def test_the_master_board_downgrade_removes_the_table(migrated):
    """Lossy, and nothing re-syncs it: the board was typed, not imported."""
    config, engine = migrated
    command.upgrade(config, MASTER)
    _seed_master_entry(engine)

    command.downgrade(config, MASTER_BEFORE)

    assert "master_rank_entry" not in _table_names(engine)


def test_the_master_board_upgrade_downgrade_upgrade_leaves_a_working_schema(migrated):
    config, engine = migrated
    command.upgrade(config, MASTER)
    _seed_master_entry(engine)

    command.downgrade(config, MASTER_BEFORE)
    command.upgrade(config, MASTER)
    _seed_master_entry(engine)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM master_rank_entry")) == 1


def test_a_from_scratch_apply_reaches_the_master_board_table(migrated):
    """A cold database, all the way up, on SQLite — `make migrate` is still the Postgres check."""
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert "master_rank_entry" in _table_names(engine)


# --- master_tier_break ---------------------------------------------------------------------------

# The revision that added where our board breaks into tiers, and the one it sits on.
TIER_BEFORE = MASTER
TIER = "e5c18b7a2f90"


def _seed_tier_break(engine, *, scope="overall", cut_rank=1):
    """One divider. No player to seed first: a cut rank names a slot, not a person."""
    with engine.begin() as connection:
        connection.execute(
            text(f"INSERT INTO master_tier_break (scope, cut_rank) VALUES ('{scope}', {cut_rank})")
        )


def test_the_tier_break_table_arrives_keyed_and_indexed(migrated):
    config, engine = migrated

    command.upgrade(config, TIER)

    assert "master_tier_break" in _table_names(engine)
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_unique_constraints("master_tier_break")} == {
        "uq_master_tier_break_scope_cut"
    }
    assert {index["name"] for index in inspector.get_indexes("master_tier_break")} == {
        "ix_master_tier_break_scope"
    }
    nullable = {
        column["name"]: column["nullable"] for column in inspector.get_columns("master_tier_break")
    }
    # A divider is a scope and a rank, and neither half is optional.
    assert not nullable["scope"] and not nullable["cut_rank"]
    # Deliberately NOT here: a player id. A tier is a band over the ranks, not a set of players.
    assert "player_id" not in nullable


def test_one_slot_cannot_hold_two_dividers(migrated):
    """One boundary counted twice would read as a tier of nobody."""
    config, engine = migrated
    command.upgrade(config, TIER)
    _seed_tier_break(engine, scope="overall", cut_rank=12)

    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text("INSERT INTO master_tier_break (scope, cut_rank) VALUES ('overall', 12)")
        )


def test_the_same_cut_rank_in_two_scopes_is_two_different_dividers(migrated):
    """Cut ranks are scope-relative: rank 12 of the board and rank 12 of the centres."""
    config, engine = migrated
    command.upgrade(config, TIER)

    _seed_tier_break(engine, scope="overall", cut_rank=12)
    _seed_tier_break(engine, scope="C", cut_rank=12)
    _seed_tier_break(engine, scope="C", cut_rank=4)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM master_tier_break")) == 3


def test_a_board_row_and_a_divider_are_independent_of_each_other(migrated):
    """No foreign key, on purpose: a cut rank has nothing to point at but a place in a list."""
    config, engine = migrated
    command.upgrade(config, TIER)
    _seed_master_entry(engine)
    _seed_tier_break(engine, cut_rank=1)

    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys=ON"))
        connection.execute(text("DELETE FROM player WHERE espn_player_id = 1"))
        # His board row went with him; the divider did not, because it was never about him.
        assert connection.scalar(text("SELECT count(*) FROM master_rank_entry")) == 0
        assert connection.scalar(text("SELECT count(*) FROM master_tier_break")) == 1


def test_the_tier_break_downgrade_removes_the_table(migrated):
    """Mildly lossy: tiers re-derive from the value gaps, the hand-moved boundaries don't."""
    config, engine = migrated
    command.upgrade(config, TIER)
    _seed_tier_break(engine)

    command.downgrade(config, TIER_BEFORE)

    assert "master_tier_break" not in _table_names(engine)
    # The board itself is untouched by going back past the tiers.
    assert "master_rank_entry" in _table_names(engine)


def test_the_tier_break_upgrade_downgrade_upgrade_leaves_a_working_schema(migrated):
    config, engine = migrated
    command.upgrade(config, TIER)
    _seed_tier_break(engine)

    command.downgrade(config, TIER_BEFORE)
    command.upgrade(config, TIER)
    _seed_tier_break(engine)

    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM master_tier_break")) == 1


def test_a_from_scratch_apply_reaches_the_tier_break_table(migrated):
    """A cold database, all the way up, on SQLite — `make migrate` is still the Postgres check."""
    config, engine = migrated
    command.downgrade(config, "base")

    command.upgrade(config, "head")

    assert "master_tier_break" in _table_names(engine)
