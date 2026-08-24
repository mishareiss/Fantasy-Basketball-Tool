"""Name normalization and the fuzzy matcher — the layer every external source goes through.

These are the tests that decide whether an imported ADP row lands on the right player, so they
are written against the real spellings that caused trouble, not invented ones.
"""

import pytest

from app.matching import (
    METHOD_ALIAS,
    METHOD_AMBIGUOUS,
    METHOD_EXACT,
    METHOD_FUZZY,
    METHOD_NORMALIZED,
    METHOD_UNMATCHED,
    AliasIndex,
    CanonicalPlayer,
    PlayerMatcher,
    normalize_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Accents come off, including the stroked letters NFKD refuses to decompose.
        ("Luka Dončić", "luka doncic"),
        ("Nikola Jokić", "nikola jokic"),
        ("Kristaps Porziņģis", "kristaps porzingis"),
        ("Nikola Đurišić", "nikola durisic"),
        ("Dennis Schröder", "dennis schroder"),
        # Generational suffixes are noise: sources disagree about them constantly.
        ("Jaren Jackson Jr.", "jaren jackson"),
        ("Michael Porter Jr", "michael porter"),
        ("Gary Payton II", "gary payton"),
        ("Otto Porter Sr.", "otto porter"),
        ("Michael Porter Junior", "michael porter"),
        ("Robert Williams III", "robert williams"),
        # Punctuation becomes a space, so both spellings of each of these agree.
        ("De'Aaron Fox", "de aaron fox"),
        ("DeAaron Fox", "deaaron fox"),
        ("O.G. Anunoby", "o g anunoby"),
        ("Karl-Anthony Towns", "karl anthony towns"),
        ("Shai Gilgeous-Alexander", "shai gilgeous alexander"),
        # Whitespace collapses; case folds.
        ("  LeBron   JAMES ", "lebron james"),
    ],
)
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


def test_normalization_never_eats_the_whole_name():
    """A player whose surname *is* a suffix token keeps it — stripping to nothing is worse."""
    assert normalize_name("Jr.") == "jr"
    assert normalize_name("V") == "v"


@pytest.fixture
def matcher() -> PlayerMatcher:
    """A small canonical pool, deliberately including two players who share a name."""
    return PlayerMatcher(
        [
            CanonicalPlayer(1, "Luka Doncic", nba_team="LAL", positions=("PG",)),
            CanonicalPlayer(2, "Michael Porter Jr.", nba_team="BKN", positions=("SF",)),
            CanonicalPlayer(3, "O.G. Anunoby", nba_team="NYK", positions=("SF",)),
            CanonicalPlayer(4, "De'Aaron Fox", nba_team="SAS", positions=("PG",)),
            CanonicalPlayer(5, "Victor Wembanyama", nba_team="SAS", positions=("C",)),
            # The pair the team tiebreak exists for.
            CanonicalPlayer(6, "Jalen Williams", nba_team="OKC", positions=("SF",)),
            CanonicalPlayer(7, "Jalen Williams", nba_team="MEM", positions=("PG",)),
        ]
    )


def test_exact_name_wins_outright(matcher):
    result = matcher.match("Victor Wembanyama")

    assert (result.player_id, result.method, result.confidence) == (5, METHOD_EXACT, 1.0)


def test_normalized_name_catches_accents_and_suffixes(matcher):
    """The tier that does most of the real work: nba.com's spelling vs ESPN's."""
    assert matcher.match("Luka Dončić").method == METHOD_NORMALIZED
    assert matcher.match("Luka Dončić").player_id == 1
    assert matcher.match("Michael Porter").player_id == 2
    assert matcher.match("DE'AARON FOX").player_id == 4


def test_punctuation_and_spacing_alone_are_a_certainty_not_a_guess(matcher):
    """nba.com's "OG Anunoby" vs ESPN's "O.G. Anunoby": identical letters, so no guessing.

    This is the most common way two sources disagree, and resolving it by similarity score
    would be a mistake — the same score band also contains "Mike Brown" against "Mikel Brown",
    who are two different people. Identical letters cannot make that error.
    """
    for spelling in ("OG Anunoby", "O G Anunoby", "o.g. anunoby"):
        result = matcher.match(spelling)
        assert (result.player_id, result.method, result.confidence) == (3, METHOD_NORMALIZED, 1.0)

    assert matcher.match("DeAaron Fox").player_id == 4


def test_a_one_letter_difference_is_still_only_a_guess(matcher):
    """The counter-example that keeps the tier above honest: same score band, different people."""
    assert matcher.match("Mike Porter").method != METHOD_NORMALIZED


def test_fuzzy_survives_a_typo(matcher):
    result = matcher.match("Victor Wembanyma")

    assert result.player_id == 5
    assert result.method == METHOD_FUZZY


def test_a_different_player_is_rejected_not_guessed(matcher):
    """Below the threshold we return nothing. A wrong match is far worse than no match."""
    result = matcher.match("Jalen Green")

    assert result.player_id is None
    assert result.method == METHOD_UNMATCHED
    assert result.confidence < 0.88


def test_a_name_nobody_shares_is_unmatched(matcher):
    result = matcher.match("Stephen Curry")

    assert result.player_id is None
    assert result.method == METHOD_UNMATCHED


def test_two_players_with_one_name_are_ambiguous(matcher):
    """No hint, no guess: both Jalen Williamses are plausible, so neither is chosen."""
    result = matcher.match("Jalen Williams")

    assert result.player_id is None
    assert result.method == METHOD_AMBIGUOUS
    assert {candidate.player_id for candidate in result.candidates} == {6, 7}


def test_team_breaks_the_tie(matcher):
    assert matcher.match("Jalen Williams", team="OKC").player_id == 6
    assert matcher.match("Jalen Williams", team="MEM").player_id == 7


def test_position_breaks_the_tie_when_team_is_unknown(matcher):
    assert matcher.match("Jalen Williams", positions=["PG"]).player_id == 7


def test_an_unhelpful_hint_leaves_it_ambiguous(matcher):
    """A team neither of them plays for narrows nothing — still no guess."""
    assert matcher.match("Jalen Williams", team="BOS").method == METHOD_AMBIGUOUS


def test_a_recorded_alias_short_circuits_everything():
    """The escape hatch: once a human says so, no similarity score gets a vote."""
    aliases = AliasIndex()
    aliases.add("hashtag", "Bones Hyland", "hyland-01", 42)
    matcher = PlayerMatcher(
        [CanonicalPlayer(42, "Nah'Shon Hyland", nba_team="MIN")], aliases=aliases
    )

    result = matcher.match("Bones Hyland", source="hashtag")

    assert (result.player_id, result.method, result.confidence) == (42, METHOD_ALIAS, 1.0)
    # ...and without the alias, nothing would have resolved it.
    assert PlayerMatcher(matcher.players).match("Bones Hyland").player_id is None


def test_an_alias_matches_on_the_source_id_too():
    aliases = AliasIndex()
    aliases.add("nba_api", "Gary Payton II", "1627780", 7)
    matcher = PlayerMatcher([CanonicalPlayer(7, "Gary Payton II")], aliases=aliases)

    result = matcher.match("Somebody Else", source="nba_api", source_id="1627780")

    assert (result.player_id, result.method) == (7, METHOD_ALIAS)


def test_an_alias_from_another_source_does_not_leak(matcher):
    aliases = AliasIndex()
    aliases.add("fantasypros", "Vic Wemby", None, 5)
    scoped = PlayerMatcher(matcher.players, aliases=aliases)

    assert scoped.match("Vic Wemby", source="fantasypros").player_id == 5
    assert scoped.match("Vic Wemby", source="hashtag").player_id is None


def test_a_last_comma_first_export_still_resolves(matcher):
    """CSV exports write it this way, and it is not a genuinely hard match."""
    assert matcher.match("Anunoby, O.G.").player_id == 3
    assert matcher.match("Doncic, Luka").player_id == 1


def test_the_threshold_is_adjustable(matcher):
    """Kept swappable on purpose: an importer with a human in the loop can be looser."""
    # "Mike Porter" scores 0.80 against "Michael Porter Jr." — a plausible nickname, but not
    # one to accept unattended, because "Mike Brown" scores 0.95 against "Mikel Brown Jr." and
    # those are two different people.
    assert matcher.match("Mike Porter").player_id is None
    loose = PlayerMatcher(matcher.players, threshold=0.7)

    assert loose.match("Mike Porter").player_id == 2


def test_an_empty_name_is_unmatched_not_an_error(matcher):
    assert matcher.match("   ").method == METHOD_UNMATCHED
