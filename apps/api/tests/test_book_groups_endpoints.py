"""Tests for book group endpoints + repository publisher-scoping (mocked)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.main import app


def _fake_group(group_id: int = 1, books: list | None = None) -> MagicMock:
    group = MagicMock()
    group.id = group_id
    group.name = "English File Elementary"
    group.publisher_id = 1
    group.books = books if books is not None else []
    group.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    group.updated_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return group


# --- auth ---------------------------------------------------------------


def test_create_group_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/book-groups", json={"name": "x", "publisher_id": 1})
    assert r.status_code in {401, 403}


def test_list_groups_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/book-groups").status_code in {401, 403}


def test_get_group_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    assert client.get("/book-groups/1").status_code in {401, 403}


# --- create -------------------------------------------------------------


@patch("app.routers.book_groups._require_admin")
@patch("app.routers.book_groups._publisher_repository")
@patch("app.routers.book_groups._group_repository")
@patch("app.routers.book_groups.get_db")
def test_create_group_success(
    mock_db: MagicMock, mock_groups: MagicMock, mock_pubs: MagicMock, mock_auth: MagicMock
) -> None:
    mock_auth.return_value = 1
    mock_db.return_value = MagicMock()
    mock_pubs.get.return_value = MagicMock()  # publisher exists
    mock_groups.create.return_value = _fake_group()

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/book-groups",
        headers={"Authorization": "Bearer mock"},
        json={"name": "English File Elementary", "publisher_id": 1},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"] == 1
    assert body["book_count"] == 0


@patch("app.routers.book_groups._require_admin")
@patch("app.routers.book_groups._publisher_repository")
@patch("app.routers.book_groups.get_db")
def test_create_group_unknown_publisher_404(
    mock_db: MagicMock, mock_pubs: MagicMock, mock_auth: MagicMock
) -> None:
    mock_auth.return_value = 1
    mock_db.return_value = MagicMock()
    mock_pubs.get.return_value = None  # publisher missing

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/book-groups",
        headers={"Authorization": "Bearer mock"},
        json={"name": "x", "publisher_id": 999},
    )
    assert r.status_code == 404


@patch("app.routers.book_groups._require_admin")
@patch("app.routers.book_groups._group_repository")
@patch("app.routers.book_groups.get_db")
def test_get_unknown_group_404(
    mock_db: MagicMock, mock_groups: MagicMock, mock_auth: MagicMock
) -> None:
    mock_auth.return_value = 1
    mock_db.return_value = MagicMock()
    mock_groups.get_with_books.return_value = None

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/book-groups/123", headers={"Authorization": "Bearer mock"})
    assert r.status_code == 404


# --- repository publisher-scoping --------------------------------------


def test_add_books_only_attaches_same_publisher_books() -> None:
    """add_books must filter to the group's publisher (query-level) and set
    group_id only on the returned books."""
    from app.repositories.book_group import BookGroupRepository

    group = MagicMock()
    group.id = 7
    group.publisher_id = 1

    # Simulate the query returning only same-publisher books.
    book_a = MagicMock(id=10, publisher_id=1, group_id=None)
    session = MagicMock()
    session.scalars.return_value.all.return_value = [book_a]

    added = BookGroupRepository().add_books(session, group, [10, 11])

    assert added == [book_a]
    assert book_a.group_id == 7
    session.commit.assert_called_once()


def test_remove_book_rejects_non_member() -> None:
    from app.repositories.book_group import BookGroupRepository

    session = MagicMock()
    other = MagicMock()
    other.group_id = 99  # belongs to a different group
    session.get.return_value = other

    assert BookGroupRepository().remove_book(session, group_id=7, book_id=10) is False
    session.commit.assert_not_called()


# --- group bundle cleanup on delete -------------------------------------


@patch("app.routers.book_groups._require_admin")
@patch("app.routers.book_groups.delete_prefix_directly")
@patch("app.routers.book_groups.get_minio_client")
@patch("app.routers.book_groups._bundle_repository")
@patch("app.routers.book_groups._publisher_repository")
@patch("app.routers.book_groups._group_repository")
@patch("app.routers.book_groups.get_db")
def test_delete_group_removes_its_bundle(
    mock_db: MagicMock,
    mock_groups: MagicMock,
    mock_pubs: MagicMock,
    mock_bundles: MagicMock,
    mock_minio: MagicMock,
    mock_delete_prefix: MagicMock,
    mock_auth: MagicMock,
) -> None:
    mock_auth.return_value = 1
    mock_db.return_value = MagicMock()
    mock_groups.get.return_value = _fake_group(7)
    mock_groups.get.return_value.name = "Chase 5 Set"
    publisher = MagicMock()
    publisher.slug = "uni"
    mock_pubs.get.return_value = publisher

    client = TestClient(app, raise_server_exceptions=False)
    r = client.delete("/book-groups/7", headers={"Authorization": "Bearer mock"})

    assert r.status_code == 204
    # Group name is normalized into the R2 path (spaces -> underscores).
    expected_prefix = "bundles/uni/Chase_5_Set/"
    assert mock_delete_prefix.call_args.kwargs["prefix"] == expected_prefix
    mock_bundles.delete_by_prefix.assert_called_once()
    assert mock_bundles.delete_by_prefix.call_args.args[1] == expected_prefix
    mock_groups.delete.assert_called_once()
