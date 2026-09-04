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
