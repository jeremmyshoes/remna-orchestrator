"""Tests for the web UI routes."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _env(tmp_path) -> None:
    os.environ["CLOUD_PROVIDER"] = "none"
    os.environ["DNS_PROVIDER"] = "none"
    os.environ["API_TOKEN"] = "test-token"
    os.environ["REMNAWAVE_API_TOKEN"] = "test"
    os.environ["UI_ENABLED"] = "true"
    os.environ["UI_SESSION_SECRET"] = "test-secret-long-enough"
    # Use a fresh SQLite DB in a tmp directory for each test
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"

    from app.core.config import get_settings
    get_settings.cache_clear()
    # Reset the cached async engine so it picks up the new DATABASE_URL
    from app.models import base as base_mod
    base_mod._engine = None
    base_mod._session_factory = None


@pytest.fixture(autouse=True)
def _setup_db(_env):
    import asyncio

    from app.models.base import create_all

    asyncio.get_event_loop().run_until_complete(create_all())


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.api.app import create_app

    app = create_app()
    return TestClient(app)


def test_ui_redirects_to_login_when_anonymous(client) -> None:
    r = client.get("/ui/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/login"


def test_ui_login_page_renders(client) -> None:
    r = client.get("/ui/login")
    assert r.status_code == 200
    assert "sign in" in r.text
    assert "API_TOKEN" in r.text


def test_ui_login_accepts_correct_token(client) -> None:
    r = client.post("/ui/login", data={"token": "test-token"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/"
    # session cookie set
    assert "remna_session" in r.cookies


def test_ui_login_rejects_bad_token(client) -> None:
    r = client.post("/ui/login", data={"token": "wrong"}, follow_redirects=False)
    assert r.status_code == 303
    assert "error" in r.headers["location"]


def test_ui_dashboard_requires_session(client) -> None:
    # login first
    client.post("/ui/login", data={"token": "test-token"})
    r = client.get("/ui/")
    assert r.status_code == 200
    assert "Nodes" in r.text
    assert "Recent rotations" in r.text


def test_ui_logout_clears_session(client) -> None:
    client.post("/ui/login", data={"token": "test-token"})
    r = client.get("/ui/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/ui/login"
