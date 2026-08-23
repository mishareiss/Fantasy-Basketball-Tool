"""Smoke tests: the app imports cleanly and the liveness endpoints answer."""

import importlib

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_app_imports_cleanly():
    for module in (
        "app.config",
        "app.db.base",
        "app.db.models",
        "app.db.session",
        "app.main",
    ):
        assert importlib.import_module(module) is not None


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_root_returns_service_info():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["name"] == app.title
