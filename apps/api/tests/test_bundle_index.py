"""Tests for the bundle index: reconcile three-way diff + endpoint auth."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import app.services.standalone_apps as sa
from app.main import app
from app.services.standalone_apps import BundleMetadata, reconcile_bundles


class _FakeRepo:
    """In-memory stand-in for BundleRepository, keyed by object_name."""

    def __init__(self, rows: list) -> None:
        self.rows = {r.object_name: r for r in rows}

    def list_all(self, _session) -> list:
        return list(self.rows.values())

    def get_by_object_name(self, _session, object_name: str):
        return self.rows.get(object_name)

    def upsert(self, _session, *, object_name: str, **fields):
        self.rows[object_name] = SimpleNamespace(object_name=object_name, **fields)

    def delete_by_object_name(self, _session, object_name: str) -> int:
        return 1 if self.rows.pop(object_name, None) is not None else 0


def _r2(object_name: str, *, size: int, version: str | None) -> BundleMetadata:
    return BundleMetadata(
        publisher_name="acme",
        book_name="book-a",
        platform="mac",
        file_name=object_name.split("/")[-1],
        file_size=size,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        object_name=object_name,
        download_url=None,
        version=version,
        stale=None,
    )


def _db_row(object_name: str, *, size: int, version: str | None):
    return SimpleNamespace(
        object_name=object_name,
        publisher_slug="acme",
        book_name="book-a",
        platform="mac",
        file_name=object_name.split("/")[-1],
        file_size=size,
        app_version=version,
    )


def test_reconcile_three_way_diff(monkeypatch) -> None:
    # DB index before: A (matches), B (size will differ -> update), D (gone from R2 -> remove)
    db_rows = [
        _db_row("bundles/acme/book-a/A.zip", size=100, version="1.0"),
        _db_row("bundles/acme/book-a/B.zip", size=200, version="1.0"),
        _db_row("bundles/acme/book-a/D.zip", size=400, version="1.0"),
    ]
    repo = _FakeRepo(db_rows)

    # R2 truth: A (unchanged), B (changed size), C (new -> create)
    r2 = [
        _r2("bundles/acme/book-a/A.zip", size=100, version="1.0"),
        _r2("bundles/acme/book-a/B.zip", size=222, version="1.0"),
        _r2("bundles/acme/book-a/C.zip", size=300, version="1.0"),
    ]
    monkeypatch.setattr(sa, "list_bundles", lambda *a, **k: r2)

    result = reconcile_bundles(MagicMock(), repo, client=MagicMock(), external_client=MagicMock(), bucket="apps")

    assert result == {"created": 1, "updated": 1, "removed": 1, "total": 3}
    # Index now mirrors R2 exactly.
    assert set(repo.rows) == {
        "bundles/acme/book-a/A.zip",
        "bundles/acme/book-a/B.zip",
        "bundles/acme/book-a/C.zip",
    }
    assert repo.rows["bundles/acme/book-a/B.zip"].file_size == 222


def test_reconcile_noop_when_in_sync(monkeypatch) -> None:
    rows = [_db_row("bundles/acme/book-a/A.zip", size=100, version="1.0")]
    repo = _FakeRepo(rows)
    monkeypatch.setattr(sa, "list_bundles", lambda *a, **k: [_r2("bundles/acme/book-a/A.zip", size=100, version="1.0")])

    result = reconcile_bundles(MagicMock(), repo, client=MagicMock(), external_client=MagicMock(), bucket="apps")

    assert result == {"created": 0, "updated": 0, "removed": 0, "total": 1}


def test_reconcile_endpoint_requires_authentication() -> None:
    client = TestClient(app, raise_server_exceptions=False)
    response = client.post("/standalone-apps/bundles/reconcile")
    assert response.status_code in {401, 403}


def test_delete_by_prefix_escapes_like_wildcards() -> None:
    """Underscores in book names must not act as LIKE single-char wildcards."""
    from sqlalchemy.dialects import postgresql

    from app.repositories.bundle import BundleRepository

    captured: dict[str, str] = {}

    def _exec(stmt):
        captured["sql"] = str(
            stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
        )
        result = MagicMock()
        result.rowcount = 0
        return result

    session = MagicMock()
    session.execute.side_effect = _exec

    BundleRepository().delete_by_prefix(session, "bundles/acme/Countdown_2_Sb/")

    # autoescape renders an ESCAPE clause so '_' is treated literally, not as a
    # wildcard that would match sibling books (Countdown-2-Sb, CountdownX2XSb...).
    assert "ESCAPE" in captured["sql"]
