"""The shared-password gate: off unless configured, and never in front of a health check.

The first test in here is the one that matters most to the other 900: with no token set, a
protected endpoint answers a request that carries no header at all. That is the promise the
rest of this suite is standing on — every other test file predates the gate and sends no
`Authorization`.
"""

import pytest
from fastapi.testclient import TestClient

from app.auth import OPEN_PATHS
from app.config import get_settings
from app.db.session import get_db
from app.main import app

TOKEN = "s3cret-shared-password"

# A route the gate is in front of, and a cheap one: `/import/kinds` reads no database and
# takes no parameters, so a 200 here is the gate letting the request through and nothing else.
PROTECTED_PATH = "/import/kinds"


@pytest.fixture
def api(db):
    """A TestClient on the throwaway SQLite session, so `/health/db` has a database to reach."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def gated():
    """Turn the gate on for one test, and put the setting back however the test ends.

    Mutating the cached `Settings` is how the rest of this suite pins its env-driven values
    (see `conftest.pinned_settings`); the dependency resolves `get_settings()` per request, so
    this takes effect on the next call and nothing needs rebuilding.
    """
    settings = get_settings()
    original = settings.app_access_token
    settings.app_access_token = TOKEN
    try:
        yield TOKEN
    finally:
        settings.app_access_token = original


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Unset means open ---------------------------------------------------------------------


def test_no_token_configured_leaves_every_endpoint_open(api):
    """The compatibility promise: no APP_ACCESS_TOKEN, no header, still a 200.

    This is what keeps the pre-existing suite green and local development unchanged.
    """
    assert get_settings().app_access_token is None
    assert api.get(PROTECTED_PATH).status_code == 200


def test_no_token_configured_ignores_a_header_it_was_sent_anyway(api):
    """A stale token in a browser's localStorage must not lock an open backend out."""
    assert api.get(PROTECTED_PATH, headers=bearer("whatever")).status_code == 200


# --- Set means required ------------------------------------------------------------------


def test_protected_route_401s_without_a_header(api, gated):
    response = api.get(PROTECTED_PATH)
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing or invalid access token."
    # The header a client is entitled to see, so "which scheme?" is never a guess.
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header",
    [
        {"Authorization": f"Bearer {TOKEN}x"},  # close, but not it
        {"Authorization": f"Bearer {TOKEN[:-1]}"},  # a truncated paste
        {"Authorization": TOKEN},  # no scheme
        {"Authorization": f"Basic {TOKEN}"},  # the wrong scheme
        {"Authorization": "Bearer "},  # empty credentials
        {"X-Access-Token": TOKEN},  # the right token in the wrong place
    ],
)
def test_protected_route_401s_on_a_wrong_header(api, gated, header):
    assert api.get(PROTECTED_PATH, headers=header).status_code == 401


def test_protected_route_200s_with_the_right_bearer_token(api, gated):
    assert api.get(PROTECTED_PATH, headers=bearer(gated)).status_code == 200


def test_the_gate_covers_writes_as_well_as_reads(api, gated):
    """A POST is the case the gate exists for: these endpoints rewrite the board."""
    assert api.post("/sync/league").status_code == 401
    assert api.get("/master/board").status_code == 401
    assert api.get("/draft").status_code == 401


def test_a_configured_token_with_non_ascii_in_it_refuses_rather_than_crashing(api):
    """`compare_digest` refuses a non-ASCII `str`, and a password someone invents may hold one.

    Both halves are compared as bytes, so an accented APP_ACCESS_TOKEN answers a plain 401
    instead of raising a TypeError into a 500. It can never answer 200: an HTTP header value is
    a byte string, so no client can transmit the matching credential in the first place — which
    is why `.env.example` and DEPLOY.md both say to keep the token ASCII.
    """
    settings = get_settings()
    original = settings.app_access_token
    settings.app_access_token = "pässwörd-ünd-so"
    try:
        assert api.get(PROTECTED_PATH, headers=bearer("password-und-so")).status_code == 401
    finally:
        settings.app_access_token = original


# --- The exempt list ---------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted(OPEN_PATHS))
def test_open_paths_answer_without_a_token(api, gated, path):
    """Render's health check and the frontend's status strip have no password to send."""
    response = api.get(path)
    assert response.status_code == 200, path
    assert response.json()["status" if path.startswith("/health") else "name"]


def test_health_db_still_reports_the_database_through_the_gate(api, gated):
    assert api.get("/health/db").json() == {"status": "ok", "database": "connected"}


def test_a_trailing_slash_is_a_redirect_rather_than_a_way_around_the_gate(api, gated):
    """Why `OPEN_PATHS` can be exact paths: Starlette normalizes before a dependency runs.

    `/import/kinds/` redirects too, and the client then re-requests the canonical path — which
    the gate is in front of.
    """
    assert api.get("/health/", follow_redirects=False).status_code == 307
    assert api.get("/import/kinds/", follow_redirects=False).status_code == 307
    assert api.get("/import/kinds/").status_code == 401
