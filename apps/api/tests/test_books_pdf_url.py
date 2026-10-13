"""``GET /books/{id}/pdf-url`` — raw PDFs of PDF books and of additional
resources (original / answered), never of a top-level flowbook."""

from __future__ import annotations

from unittest.mock import MagicMock
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from minio.error import S3Error
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import create_access_token
from app.db import get_db
from app.db.base import Base
from app.main import app
from app.models.book import Book
from app.models.publisher import Publisher
from app.models.user import User
from app.routers import books as books_router

engine = create_engine(
    "sqlite+pysqlite:///:memory:",
    future=True,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def override_get_db():
    with TestingSessionLocal() as session:
        yield session


@pytest.fixture(scope="module", autouse=True)
def setup_database():
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def seeded():
    with TestingSessionLocal() as session:
        session.query(Book).delete()
        session.query(Publisher).delete()
        session.query(User).delete()
        user = User(email="admin@example.com", hashed_password="hashed")
        pub = Publisher(name="Universal", slug="universal-elt")
        session.add_all([user, pub])
        session.flush()
        common = {"language": "en", "category": "reader", "publisher_id": pub.id}
        parent = Book(book_name="Jane_Eyre", book_type="standard", **common)
        session.add(parent)
        session.flush()
        child = Book(book_name="Quizzes", book_type="standard", parent_book_id=parent.id, **common)
        session.add(child)
        session.commit()
        ids = {"parent": parent.id, "child": child.id}
        headers = {"Authorization": f"Bearer {create_access_token(subject=str(user.id))}"}
    return ids, headers


@pytest.fixture
def storage(monkeypatch):
    present = {
        "universal-elt/books/Jane_Eyre/additional-resources/Quizzes/raw/original.pdf",
        "universal-elt/books/Jane_Eyre/additional-resources/Quizzes/raw/answered.pdf",
    }
    internal = MagicMock()

    def stat(bucket, name):
        if name not in present:
            raise S3Error(MagicMock(), "NoSuchKey", "missing", name, "req", "host")
        return MagicMock()

    internal.stat_object.side_effect = stat
    external = MagicMock()
    external.presigned_get_object.side_effect = (
        lambda bucket, name, expires, response_headers=None: f"https://r2/{name}?h={response_headers}"
    )
    monkeypatch.setattr(books_router, "get_minio_client", lambda settings: internal)
    monkeypatch.setattr("app.services.get_minio_client_external", lambda settings: external)
    return present, external


@pytest.mark.parametrize("variant", ["original", "answered"])
def test_child_flowbook_signs_requested_variant(seeded, storage, variant):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url", params={"variant": variant}, headers=headers
    )
    assert res.status_code == 200
    assert f"/Quizzes/raw/{variant}.pdf" in res.json()["download_url"]


def test_child_flowbook_defaults_to_original(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(f"/books/{ids['child']}/pdf-url", headers=headers)
    assert res.status_code == 200
    assert res.json()["download_url"].split("?")[0].endswith("/raw/original.pdf")


def test_missing_variant_is_404(seeded, storage):
    present, _ = storage
    present.discard("universal-elt/books/Jane_Eyre/additional-resources/Quizzes/raw/answered.pdf")
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url", params={"variant": "answered"}, headers=headers
    )
    assert res.status_code == 404


def test_top_level_flowbook_pdf_is_not_handed_out(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['parent']}/pdf-url", params={"variant": "original"}, headers=headers
    )
    assert res.status_code == 400
    _, external = storage
    external.presigned_get_object.assert_not_called()


def test_unknown_variant_rejected(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url", params={"variant": "../../x"}, headers=headers
    )
    assert res.status_code == 422


def test_filename_becomes_attachment_header(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url",
        params={"variant": "answered", "filename": 'Jane Eyre - Quizzes "Cevaplı"/../'},
        headers=headers,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["filename"] == "Jane Eyre - Quizzes Cevaplı...pdf"
    header = unquote(body["download_url"].split("?h=", 1)[1])
    assert "attachment;" in header
    assert "Cevaplı" in header  # filename* keeps the Turkish letter
    assert "Cevapli" in header  # ASCII fallback for old clients


def test_inline_opens_in_the_browser_viewer(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url",
        params={"variant": "original", "filename": "Jane Eyre - Quizzes", "disposition": "inline"},
        headers=headers,
    )
    assert res.status_code == 200
    header = unquote(res.json()["download_url"].split("?h=", 1)[1])
    assert "inline; filename=" in header
    assert "attachment" not in header
    assert "application/pdf" in header


def test_unknown_disposition_rejected(seeded, storage):
    ids, headers = seeded
    res = TestClient(app).get(
        f"/books/{ids['child']}/pdf-url", params={"disposition": "evil"}, headers=headers
    )
    assert res.status_code == 422
