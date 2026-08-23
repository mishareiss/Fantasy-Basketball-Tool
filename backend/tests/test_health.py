"""Smoke tests: the app imports cleanly and the liveness endpoints answer."""

import importlib

import pytest
from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app

client = TestClient(app)


@pytest.fixture
def api(db):
    """A TestClient whose requests use the throwaway SQLite session."""
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_app_imports_cleanly():
    for module in (
        "app.config",
        "app.db.base",
        "app.db.models",
        "app.db.session",
        "app.espn",
        "app.main",
        "app.scoring",
    ):
        assert importlib.import_module(module) is not None


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_db_round_trips_a_query(api):
    response = api.get("/health/db")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "connected"}


def test_root_returns_service_info():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == app.title
