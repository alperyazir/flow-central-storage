"""Tests for application settings endpoints (mocked repository/auth)."""

from __future__ import annotations

from unittest.mock import ANY, MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def test_get_settings_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/settings")
    assert response.status_code in {401, 403}


def test_update_settings_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.put("/settings", json={"default_auto_bundle": False})
    assert response.status_code in {401, 403}


@patch("app.routers.settings._require_admin")
@patch("app.routers.settings._settings_repository")
@patch("app.routers.settings.get_db")
def test_get_settings_returns_defaults_when_unset(
    mock_get_db: MagicMock,
    mock_repo: MagicMock,
    mock_auth: MagicMock,
) -> None:
    mock_auth.return_value = 1
    mock_get_db.return_value = MagicMock()
    mock_repo.get_all.return_value = {}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/settings", headers={"Authorization": "Bearer mock"})

    assert response.status_code == 200
    # Default for default_auto_bundle is True.
    assert response.json()["default_auto_bundle"] is True


@patch("app.routers.settings._require_admin")
@patch("app.routers.settings._settings_repository")
@patch("app.routers.settings.get_db")
def test_get_settings_applies_stored_override(
    mock_get_db: MagicMock,
    mock_repo: MagicMock,
    mock_auth: MagicMock,
) -> None:
    mock_auth.return_value = 1
    mock_get_db.return_value = MagicMock()
    mock_repo.get_all.return_value = {"default_auto_bundle": False}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/settings", headers={"Authorization": "Bearer mock"})

    assert response.status_code == 200
    assert response.json()["default_auto_bundle"] is False


@patch("app.routers.settings._require_admin")
@patch("app.routers.settings._settings_repository")
@patch("app.routers.settings.get_db")
def test_update_settings_persists_and_returns_merged(
    mock_get_db: MagicMock,
    mock_repo: MagicMock,
    mock_auth: MagicMock,
) -> None:
    mock_auth.return_value = 1
    mock_get_db.return_value = MagicMock()
    # After the update is applied, get_all reflects the new value.
    mock_repo.get_all.return_value = {"default_auto_bundle": False}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.put(
        "/settings",
        headers={"Authorization": "Bearer mock"},
        json={"default_auto_bundle": False},
    )

    assert response.status_code == 200
    assert response.json()["default_auto_bundle"] is False
    mock_repo.set_many.assert_called_once_with(ANY, {"default_auto_bundle": False})


@patch("app.routers.settings._require_admin")
@patch("app.routers.settings._settings_repository")
@patch("app.routers.settings.get_db")
def test_update_settings_ignores_omitted_fields(
    mock_get_db: MagicMock,
    mock_repo: MagicMock,
    mock_auth: MagicMock,
) -> None:
    mock_auth.return_value = 1
    mock_get_db.return_value = MagicMock()
    mock_repo.get_all.return_value = {}

    client = TestClient(app, raise_server_exceptions=False)
    response = client.put(
        "/settings",
        headers={"Authorization": "Bearer mock"},
        json={},
    )

    assert response.status_code == 200
    # Nothing to persist when no fields are provided.
    mock_repo.set_many.assert_not_called()
