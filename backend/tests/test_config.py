"""Settings that have to survive being typed into a hosting dashboard.

`CORS_ORIGINS` is a list, and a list-typed pydantic setting accepts only JSON from the
environment by default — paste `https://a,https://b` into Render's env editor and the process
dies at import with a SettingsError rather than serving with the wrong origins. The validator in
`app.config` is what makes both forms work; these are the tests that say so.

`Settings(...)` is constructed directly here, never `get_settings()`: the cached instance is the
one the rest of the suite has pinned, and re-reading the repo's real `.env` mid-suite is exactly
what these tests must not do.
"""

import re

import pytest
from pydantic import ValidationError

from app.config import REPO_ROOT, Settings


def settings(**overrides) -> Settings:
    """A Settings built from explicit values, with the repo `.env` out of the picture.

    `_env_file=None` is pydantic-settings' own escape hatch; without it a developer's local
    CORS_ORIGINS or APP_ACCESS_TOKEN would decide what these tests assert.
    """
    return Settings(_env_file=None, **overrides)


# --- CORS_ORIGINS -------------------------------------------------------------------------


def test_cors_origins_defaults_to_the_next_dev_server():
    assert settings().cors_origins == ["http://localhost:3000"]


def test_cors_origins_splits_a_comma_separated_string():
    """The form a deploy dashboard wants: one key, one value, no JSON punctuation."""
    parsed = settings(cors_origins="https://board.vercel.app,https://fbb.example.com")
    assert parsed.cors_origins == ["https://board.vercel.app", "https://fbb.example.com"]


def test_cors_origins_tolerates_the_spaces_a_person_types():
    parsed = settings(cors_origins=" https://a.vercel.app , https://b.vercel.app , ")
    assert parsed.cors_origins == ["https://a.vercel.app", "https://b.vercel.app"]


def test_cors_origins_accepts_a_single_origin_with_no_comma_at_all():
    assert settings(cors_origins="https://board.vercel.app").cors_origins == [
        "https://board.vercel.app"
    ]


def test_cors_origins_still_accepts_the_json_form_env_example_documents():
    parsed = settings(cors_origins='["http://localhost:3000", "https://board.vercel.app"]')
    assert parsed.cors_origins == ["http://localhost:3000", "https://board.vercel.app"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://a.vercel.app,https://b.vercel.app",
            ["https://a.vercel.app", "https://b.vercel.app"],
        ),
        ("https://a.vercel.app", ["https://a.vercel.app"]),
        ('["https://a.vercel.app"]', ["https://a.vercel.app"]),
    ],
)
def test_cors_origins_reads_both_forms_from_the_actual_ENVIRONMENT(monkeypatch, value, expected):
    """The test that proves `NoDecode`, and the reason the annotation is there at all.

    Every case above hands the value in as a keyword, which never went through pydantic-settings'
    JSON decoding. `CORS_ORIGINS` in the environment is the path that raised SettingsError on a
    bare string before the field was annotated — and the only path Render will ever use.
    """
    monkeypatch.setenv("CORS_ORIGINS", value)
    assert Settings(_env_file=None).cors_origins == expected


def test_app_access_token_reads_from_the_environment(monkeypatch):
    monkeypatch.setenv("APP_ACCESS_TOKEN", "from-the-dashboard")
    assert Settings(_env_file=None).app_access_token == "from-the-dashboard"


def test_cors_origins_accepts_an_already_parsed_list_untouched():
    """The in-code path — a test or a REPL assigning the list directly."""
    assert settings(cors_origins=["https://a"]).cors_origins == ["https://a"]


def test_an_empty_cors_origins_allows_nothing_rather_than_quietly_defaulting():
    """Silently falling back to localhost would look like the setting had worked."""
    assert settings(cors_origins="").cors_origins == []
    assert settings(cors_origins="   ").cors_origins == []


def test_malformed_json_in_cors_origins_fails_loudly():
    """A half-typed JSON array is a mistake, and a startup crash is the kindest report of it."""
    with pytest.raises((ValidationError, ValueError)):
        settings(cors_origins='["https://a"')


# --- APP_ACCESS_TOKEN ---------------------------------------------------------------------


def test_app_access_token_is_unset_by_default():
    """Unset is what `app.auth` reads as "the gate is off", so this default is load-bearing."""
    assert settings().app_access_token is None


def test_app_access_token_is_taken_verbatim():
    """No stripping, no casing: the token is compared byte for byte against what a client sends."""
    assert settings(app_access_token=" Sp3cial-Token ").app_access_token == " Sp3cial-Token "


# --- The deploy blueprint and these settings are the same list ----------------------------


def test_every_key_render_yaml_declares_is_a_setting_we_actually_read():
    """`render.yaml` and `Settings` cannot drift apart without this failing.

    A key misspelled in the blueprint is a value pasted into the dashboard that nothing ever
    reads — a deploy that comes up green and then can't reach ESPN. Read with a regex rather
    than a YAML parser on purpose: this is worth asserting without adding a dependency the
    application itself doesn't have.
    """
    blueprint = (REPO_ROOT / "render.yaml").read_text()
    declared = set(re.findall(r"- key: (\w+)", blueprint))

    assert declared, "no env keys found in render.yaml — did the format change?"
    assert {name.lower() for name in declared} <= set(Settings.model_fields), sorted(
        name for name in declared if name.lower() not in Settings.model_fields
    )


def test_render_yaml_declares_the_keys_a_deploy_cannot_work_without():
    """The other direction: the blueprint must still ASK for the values the app needs.

    Dropping `APP_ACCESS_TOKEN` from it would leave a public URL with the gate off, and
    dropping `CORS_ORIGINS` would leave the frontend unable to call the backend at all —
    neither of which announces itself as a missing line in a YAML file.
    """
    blueprint = (REPO_ROOT / "render.yaml").read_text()
    for key in (
        "DATABASE_URL",
        "APP_ACCESS_TOKEN",
        "CORS_ORIGINS",
        "ESPN_S2",
        "SWID",
        "ESPN_LEAGUE_ID",
        "ESPN_SEASON",
    ):
        assert f"- key: {key}\n" in blueprint, key


def test_render_yaml_commits_no_secret_values():
    """Every declared key is `sync: false` — "ask me in the dashboard", never a value in git.

    The ESPN cookies are account credentials and the access token is the only lock on the API,
    so a `value:` line appearing under any of them is the one mistake in this file that cannot
    be taken back by a later commit.
    """
    blueprint = (REPO_ROOT / "render.yaml").read_text()
    keys = re.findall(r"- key: \w+\n(\s+\w+:.*)", blueprint)
    assert keys, "no env keys found in render.yaml — did the format change?"
    for following_line in keys:
        assert following_line.strip() == "sync: false", following_line
