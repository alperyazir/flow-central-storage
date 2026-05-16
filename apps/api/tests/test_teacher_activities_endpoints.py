"""Tests for the per-activity teacher media endpoints."""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.db import get_db
from app.main import app


@pytest.fixture(autouse=True)
def override_dependencies(monkeypatch):
    from app.routers import teachers

    monkeypatch.setattr(teachers, "_require_admin", lambda credentials, db: 1)
    monkeypatch.setattr(
        teachers._teacher_repository,
        "get_or_create_by_teacher_id",
        lambda *args, **kwargs: SimpleNamespace(id=1, teacher_id="teacher_123"),
    )

    fake_client = MagicMock()
    fake_client.list_objects.return_value = []
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    def fake_get_db():
        yield MagicMock()

    app.dependency_overrides[get_db] = fake_get_db

    yield

    app.dependency_overrides.pop(get_db, None)


def _auth_headers() -> dict[str, str]:
    token = create_access_token(subject="1")
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# Upload
# ============================================================================


def test_upload_activity_image_success(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    file_content = b"fake png bytes"
    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/act_42/upload",
        params={"kind": "images"},
        headers=_auth_headers(),
        files={"file": ("q1.png", io.BytesIO(file_content), "image/png")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["teacher_id"] == "teacher_123"
    assert body["activity_id"] == "act_42"
    assert body["kind"] == "images"
    assert body["path"] == "images/q1.png"
    assert body["object_key"] == "teacher_123/activities/act_42/images/q1.png"
    assert body["size"] == len(file_content)
    fake_client.fput_object.assert_called_once()


def test_upload_activity_rejects_invalid_kind(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/act_42/upload",
        params={"kind": "documents"},
        headers=_auth_headers(),
        files={"file": ("foo.png", io.BytesIO(b"x"), "image/png")},
    )

    assert response.status_code == 400
    assert "kind" in response.json()["detail"].lower()
    fake_client.fput_object.assert_not_called()


def test_upload_activity_rejects_invalid_mime(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/act_42/upload",
        params={"kind": "images"},
        headers=_auth_headers(),
        files={"file": ("malware.exe", io.BytesIO(b"x"), "application/x-msdownload")},
    )

    assert response.status_code == 415
    fake_client.fput_object.assert_not_called()


def test_upload_activity_rejects_traversal(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/..%2Fother/upload",
        params={"kind": "images"},
        headers=_auth_headers(),
        files={"file": ("q1.png", io.BytesIO(b"x"), "image/png")},
    )

    assert response.status_code in (400, 404)


def test_upload_activity_requires_auth() -> None:
    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/act_42/upload",
        params={"kind": "images"},
        files={"file": ("q1.png", io.BytesIO(b"x"), "image/png")},
    )
    assert response.status_code == 403


# ============================================================================
# Presigned URL
# ============================================================================


def test_activity_presigned_success(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    fake_client.stat_object.return_value = SimpleNamespace(size=10, content_type="image/png")
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    fake_external = MagicMock()
    fake_external.presigned_get_object.return_value = "https://r2.example/signed-url"
    monkeypatch.setattr(teachers, "get_minio_client_external", lambda settings: fake_external)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/presigned",
        params={"path": "images/q1.png"},
        headers=_auth_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "https://r2.example/signed-url"
    assert body["expires_in"] == 3600

    # Object key passed to stat_object includes the activity folder.
    stat_args, _ = fake_client.stat_object.call_args
    assert "teacher_123/activities/act_42/images/q1.png" in stat_args


def test_activity_presigned_rejects_path_without_kind(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/presigned",
        params={"path": "q1.png"},
        headers=_auth_headers(),
    )

    assert response.status_code == 400
    assert "kind" in response.json()["detail"].lower()


def test_activity_presigned_404_when_missing(monkeypatch) -> None:
    from minio.error import S3Error

    from app.routers import teachers

    fake_client = MagicMock()
    fake_client.stat_object.side_effect = S3Error(
        code="NoSuchKey",
        message="missing",
        resource="x",
        request_id="x",
        host_id="x",
        response=MagicMock(),
    )
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/presigned",
        params={"path": "images/missing.png"},
        headers=_auth_headers(),
    )
    assert response.status_code == 404


def test_activity_presigned_requires_auth() -> None:
    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/presigned",
        params={"path": "images/q1.png"},
    )
    assert response.status_code == 403


# ============================================================================
# Delete folder
# ============================================================================


def test_delete_activity_folder_success(monkeypatch) -> None:
    from app.routers import teachers

    monkeypatch.setattr(
        teachers,
        "delete_prefix_directly",
        lambda client, bucket, prefix: SimpleNamespace(
            objects_removed=3, bytes_removed=1024
        ),
    )

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.delete(
        "/teachers/teacher_123/activities/act_42",
        headers=_auth_headers(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert body["teacher_id"] == "teacher_123"
    assert body["activity_id"] == "act_42"
    assert body["objects_removed"] == 3


def test_delete_activity_folder_502_on_error(monkeypatch) -> None:
    from app.routers import teachers
    from app.services import DirectDeletionError

    def boom(*args, **kwargs):
        raise DirectDeletionError("nope")

    monkeypatch.setattr(teachers, "delete_prefix_directly", boom)

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.delete(
        "/teachers/teacher_123/activities/act_42",
        headers=_auth_headers(),
    )
    assert response.status_code == 502


def test_delete_activity_folder_requires_auth() -> None:
    client = TestClient(app)
    response = client.delete("/teachers/teacher_123/activities/act_42")
    assert response.status_code == 403


# ============================================================================
# Path builder unit tests
# ============================================================================


def test_build_activity_object_key_full() -> None:
    from app.routers.teachers import _build_activity_object_key

    key = _build_activity_object_key("t1", "a1", "images", "q1.png")
    assert key == "t1/activities/a1/images/q1.png"


def test_build_activity_object_key_prefix_only() -> None:
    from app.routers.teachers import _build_activity_object_key

    assert _build_activity_object_key("t1", "a1") == "t1/activities/a1/"
    assert _build_activity_object_key("t1", "a1", "images") == "t1/activities/a1/images/"


def test_build_activity_object_key_rejects_invalid_kind() -> None:
    from fastapi import HTTPException

    from app.routers.teachers import _build_activity_object_key

    with pytest.raises(HTTPException) as exc:
        _build_activity_object_key("t1", "a1", "documents", "x.txt")
    assert exc.value.status_code == 400


def test_build_activity_object_key_rejects_traversal_filename() -> None:
    from fastapi import HTTPException

    from app.routers.teachers import _build_activity_object_key

    with pytest.raises(HTTPException) as exc:
        _build_activity_object_key("t1", "a1", "images", "../escape.png")
    assert exc.value.status_code == 400


# ============================================================================
# content.json PUT / GET
# ============================================================================


def test_put_activity_content_success(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    body = b'{"questions":[{"id":"q1"}]}'
    client = TestClient(app)
    response = client.put(
        "/teachers/teacher_123/activities/act_42/content",
        headers={**_auth_headers(), "Content-Type": "application/json"},
        content=body,
    )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["object_key"] == "teacher_123/activities/act_42/content.json"
    assert data["size"] == len(body)
    fake_client.put_object.assert_called_once()


def test_put_activity_content_rejects_empty_body(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.put(
        "/teachers/teacher_123/activities/act_42/content",
        headers=_auth_headers(),
        content=b"",
    )
    assert response.status_code == 400
    fake_client.put_object.assert_not_called()


def test_put_activity_content_requires_auth() -> None:
    client = TestClient(app)
    response = client.put(
        "/teachers/teacher_123/activities/act_42/content", content=b"{}"
    )
    assert response.status_code == 403


def test_get_activity_content_success(monkeypatch) -> None:
    from app.routers import teachers

    body = b'{"questions":[{"id":"q1","correct":"A"}]}'
    fake_obj = MagicMock()
    fake_obj.read.return_value = body
    fake_obj.close = MagicMock()
    fake_obj.release_conn = MagicMock()

    fake_client = MagicMock()
    fake_client.get_object.return_value = fake_obj
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/content",
        headers=_auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"questions": [{"id": "q1", "correct": "A"}]}
    fake_obj.close.assert_called_once()
    fake_obj.release_conn.assert_called_once()


def test_get_activity_content_404_when_missing(monkeypatch) -> None:
    from minio.error import S3Error

    from app.routers import teachers

    fake_client = MagicMock()
    fake_client.get_object.side_effect = S3Error(
        code="NoSuchKey",
        message="missing",
        resource="x",
        request_id="x",
        host_id="x",
        response=MagicMock(),
    )
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/content",
        headers=_auth_headers(),
    )
    assert response.status_code == 404


def test_get_activity_content_502_when_invalid_json(monkeypatch) -> None:
    from app.routers import teachers

    fake_obj = MagicMock()
    fake_obj.read.return_value = b"not json"
    fake_obj.close = MagicMock()
    fake_obj.release_conn = MagicMock()
    fake_client = MagicMock()
    fake_client.get_object.return_value = fake_obj
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/activities/act_42/content",
        headers=_auth_headers(),
    )
    assert response.status_code == 502


def test_get_activity_content_requires_auth() -> None:
    client = TestClient(app)
    response = client.get("/teachers/teacher_123/activities/act_42/content")
    assert response.status_code == 403


# ============================================================================
# /usage — sum of every object under the teacher namespace
# ============================================================================


def test_get_teacher_usage_sums_objects(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    fake_client.list_objects.return_value = [
        SimpleNamespace(object_name="teacher_123/materials/a.pdf", size=1000),
        SimpleNamespace(object_name="teacher_123/activities/x/images/q.png", size=500),
        # size None should be skipped, not crash.
        SimpleNamespace(object_name="teacher_123/activities/x/audio/p.mp3", size=None),
    ]
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/usage",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["used_bytes"] == 1500
    assert body["object_count"] == 3


def test_get_teacher_usage_zero_when_empty(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    fake_client.list_objects.return_value = []
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    client = TestClient(app)
    response = client.get(
        "/teachers/teacher_123/usage", headers=_auth_headers()
    )
    assert response.status_code == 200
    assert response.json()["used_bytes"] == 0
    assert response.json()["object_count"] == 0


def test_get_teacher_usage_requires_auth() -> None:
    client = TestClient(app)
    response = client.get("/teachers/teacher_123/usage")
    assert response.status_code == 403


# ============================================================================
# Upload size-limit (413)
# ============================================================================


def test_upload_activity_rejects_oversized(monkeypatch) -> None:
    from app.routers import teachers

    fake_client = MagicMock()
    monkeypatch.setattr(teachers, "get_minio_client", lambda settings: fake_client)

    # Default limit is 100MB; +1 byte triggers 413.
    big = b"x" * (104857600 + 1)
    client = TestClient(app)
    response = client.post(
        "/teachers/teacher_123/activities/act_42/upload",
        params={"kind": "video"},
        headers=_auth_headers(),
        files={"file": ("big.mp4", io.BytesIO(big), "video/mp4")},
    )
    assert response.status_code == 413
    fake_client.fput_object.assert_not_called()
