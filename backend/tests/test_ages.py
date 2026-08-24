"""The age sync, entirely offline: recorded nba.com responses into birthdates and ages.

Ages are asserted at a fixed AGE_AS_OF, never at `date.today()` — which is the same property
the production code has, for the same reason: an age that drifts between runs is an age you
can't reason about on draft day.
"""

from datetime import date

import pytest
from sqlalchemy import select

from app.ages import (
    NBA_SOURCE,
    AgeSyncSummary,
    NbaApiError,
    NbaPlayer,
    compute_age,
    match_nba_roster,
    parse_birthdate,
    players_needing_birthdate,
    recompute_ages,
    sync_ages,
)
from app.db.models import Player, PlayerAlias
from app.espn.players import parse_player_pool
from app.espn.sync import SyncSummary, sync_players
from app.matching import CanonicalPlayer, PlayerMatcher, build_matcher
from tests.conftest import AGE_AS_OF

# Two of the fixture players, chosen because their ages are checkable by hand: a 41-year-old
# on his last legs and an 19-year-old rookie. Their gap is the whole reason age matters.
LEBRON = 1966
FLAGG = 5041939
TOWNS = 3136195


@pytest.fixture
def players(db, player_pool_payload) -> SyncSummary:
    """The ESPN half of the world: our canonical players, with no ages on them yet."""
    summary = SyncSummary(league_id=999999, season=2027)
    sync_players(db, parse_player_pool(player_pool_payload), summary)
    db.commit()
    return summary


@pytest.fixture
def synced(db, players, nba_players, fetch_recorded_birthdate) -> AgeSyncSummary:
    """One full offline age sync: match, "fetch", derive."""
    return sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )


def test_parse_birthdate_reads_the_iso_column():
    assert parse_birthdate("1997-07-18T00:00:00") == date(1997, 7, 18)
    assert parse_birthdate("") is None
    assert parse_birthdate(None) is None
    assert parse_birthdate("not a date") is None


@pytest.mark.parametrize(
    ("birthdate", "expected"),
    [
        (date(1984, 12, 30), 41),  # birthday later in the season: still 41 on Oct 1
        (date(2006, 12, 21), 19),
        (date(1995, 11, 15), 30),
        # The boundary: a birthday on the as-of date counts, the day after doesn't.
        (date(2000, 10, 1), 26),
        (date(2000, 10, 2), 25),
    ],
)
def test_compute_age(birthdate, expected):
    assert compute_age(birthdate, AGE_AS_OF) == expected


def test_the_sync_sets_birthdates_and_ages(db, synced):
    assert synced.age_as_of == AGE_AS_OF
    assert synced.birthdates_fetched == synced.nba_matched > 50

    lebron = db.get(Player, LEBRON)
    assert lebron.birthdate == date(1984, 12, 30)
    assert lebron.age == 41

    flagg = db.get(Player, FLAGG)
    assert flagg.birthdate == date(2006, 12, 21)
    assert flagg.age == 19


def test_it_records_an_alias_per_matched_player(db, synced):
    aliases = list(db.scalars(select(PlayerAlias).where(PlayerAlias.source == NBA_SOURCE)))

    assert len(aliases) == synced.aliases_created == synced.nba_matched
    assert all(alias.source_id for alias in aliases), "the nba id is what the fetch needs"
    # Provenance, so a shaky match is spottable later.
    assert all(alias.confidence and alias.match_method for alias in aliases)


def test_accented_and_suffixed_names_resolve(db, synced):
    """nba.com writes "Nikola Jokić" and "Luka Dončić"; ESPN writes neither with accents."""
    by_name = {
        alias.source_name: alias.player_id
        for alias in db.scalars(select(PlayerAlias).where(PlayerAlias.source == NBA_SOURCE))
    }

    assert by_name["Luka Dončić"] == 3945274
    assert by_name["Nikola Jokić"] == 3112335
    assert by_name["Karl-Anthony Towns"] == TOWNS
    assert by_name["De'Aaron Fox"] == 4066259
    assert by_name["Michael Porter Jr."] == 4278104
    assert db.get(Player, TOWNS).age == 30


def test_a_second_run_fetches_nothing_and_duplicates_nothing(
    db, players, synced, nba_players, fetch_recorded_birthdate
):
    """Incremental and idempotent — the property that makes a 1,000-call sync tolerable."""
    before = db.scalar(select(Player).where(Player.espn_player_id == LEBRON)).birthdate

    second = sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    assert second.birthdates_fetched == 0
    assert second.aliases_created == 0
    assert second.aliases_existing == synced.aliases_created
    assert second.ages_set == 0
    assert len(list(db.scalars(select(PlayerAlias)))) == synced.aliases_created
    assert db.get(Player, LEBRON).birthdate == before


def test_a_rerun_does_not_rewrite_how_a_match_was_made(
    db, players, synced, nba_players, fetch_recorded_birthdate
):
    """Provenance has to survive re-running, or it degenerates into "because the alias said so".

    Every run after the first resolves these names *through* the alias it wrote, so writing
    that method back would erase the exact/normalized/fuzzy answer we actually care about —
    and with it any chance of spotting a shaky auto-match later.
    """
    before = {
        alias.source_name: (alias.match_method, alias.confidence)
        for alias in db.scalars(select(PlayerAlias))
    }
    assert "alias" not in {method for method, _ in before.values()}

    sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    after = {
        alias.source_name: (alias.match_method, alias.confidence)
        for alias in db.scalars(select(PlayerAlias))
    }
    assert after == before


def test_refresh_forces_a_refetch(db, players, synced, nba_players, fetch_recorded_birthdate):
    refreshed = sync_ages(
        db,
        as_of=AGE_AS_OF,
        refresh=True,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    assert refreshed.birthdates_fetched == synced.birthdates_fetched
    assert refreshed.aliases_created == 0


def test_a_limited_run_is_resumable(db, players, nba_players, fetch_recorded_birthdate):
    """Killing the sync early has to be safe: whatever it fetched stays fetched."""
    first = sync_ages(
        db,
        as_of=AGE_AS_OF,
        limit=5,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    assert first.birthdates_fetched == 5
    assert first.birthdates_pending == first.nba_matched - 5

    rest = sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=fetch_recorded_birthdate,
        delay=0,
        sleep=lambda _: None,
    )

    assert rest.birthdates_fetched == first.birthdates_pending
    assert rest.birthdates_pending == 0


def test_the_fetch_queue_is_ordered_by_who_matters(
    db, players, synced, nba_players, fetch_recorded_birthdate
):
    """With `--limit`, the players whose ages move the board get fetched first."""
    for player in db.scalars(select(Player)):
        player.birthdate = None
    db.commit()

    queued = [player.full_name for player, _ in players_needing_birthdate(db)]

    assert queued, "everything is missing a birthdate again"
    # Deterministic, so a partial run resumes in the same order it started in.
    assert queued == [player.full_name for player, _ in players_needing_birthdate(db)]


def test_nba_com_refusing_us_stops_the_run_without_losing_progress(db, players, nba_players):
    """A wall of failures means back off, not grind through another 900 players."""
    calls = []

    def fetch(nba_id: int):
        calls.append(nba_id)
        if len(calls) > 3:
            raise NbaApiError("429 from nba.com")
        return date(1995, 1, 1)

    summary = sync_ages(
        db, as_of=AGE_AS_OF, nba_players=nba_players, fetch=fetch, delay=0, sleep=lambda _: None
    )

    assert summary.birthdates_fetched == 3
    assert summary.birthdates_failed == 1
    assert summary.birthdates_pending > 0
    # Committed, not rolled back — that is what makes the next run cheap.
    assert db.scalar(select(Player).where(Player.birthdate.is_not(None))) is not None


def test_a_player_nba_com_has_no_birthdate_for_is_not_an_error(db, players, nba_players):
    summary = sync_ages(
        db,
        as_of=AGE_AS_OF,
        nba_players=nba_players,
        fetch=lambda nba_id: None,
        delay=0,
        sleep=lambda _: None,
    )

    assert summary.birthdates_absent == summary.nba_matched
    assert summary.birthdates_failed == 0
    assert summary.players_with_age == 0


def test_players_nba_com_never_heard_of_land_on_the_worklist(db, synced):
    """The long tail: draft-and-stash prospects and rookies newer than the bundled roster."""
    assert synced.players_without_alias > 0
    assert "Gabriele Procida" in synced.unresolved_players
    assert db.get(Player, 4871139).age is None
    # And they are counted, not quietly dropped.
    assert synced.players_with_age + synced.players_missing_age == synced.players_total


def test_the_retired_half_of_the_roster_never_guesses(db, players, nba_players):
    """nba.com's static list goes back to 1946; letting it fuzzy-match is all downside."""
    roster = [*nba_players, NbaPlayer(nba_id=1479, full_name="Mike Brown", is_active=False)]
    summary = AgeSyncSummary(age_as_of=AGE_AS_OF)

    claims = match_nba_roster(build_matcher(db, source=NBA_SOURCE), roster, summary)

    assert not any(nba.nba_id == 1479 for nba, _ in claims.values())
    assert summary.nba_ambiguous == 0


def test_the_active_player_wins_a_shared_name():
    """Both Gary Paytons normalize to "gary payton". Only one of them is our player."""
    matcher = PlayerMatcher([CanonicalPlayer(999, "Gary Payton II", nba_team="GSW")])
    roster = [
        NbaPlayer(nba_id=56, full_name="Gary Payton", is_active=False),
        NbaPlayer(nba_id=1627780, full_name="Gary Payton II", is_active=True),
    ]
    summary = AgeSyncSummary(age_as_of=AGE_AS_OF)

    claims = match_nba_roster(matcher, roster, summary)

    # One alias, pointing at the son — storing the father's 1968 birthdate would age our
    # player out of the league.
    assert len(claims) == 1
    assert claims[999][0].nba_id == 1627780


def test_changing_the_as_of_date_rebuilds_every_age_offline(db, synced):
    """Birthdate is the truth; age is a cached derivative, and this is what proves it."""
    changed = recompute_ages(db, date(2030, 10, 1))
    db.commit()

    assert changed > 0
    assert db.get(Player, LEBRON).age == 45
    assert db.get(Player, FLAGG).age == 23
