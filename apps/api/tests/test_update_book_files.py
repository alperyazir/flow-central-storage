"""In-place book file updates: new files land, AI output survives.

Every other upload path wipes the book's prefix first, which also wipes
ai-data/ and ai-content/. These tests pin the contract of the path that does
not: protected folders are never touched, nothing is written on a dry run,
unchanged files are skipped, pruning is opt-in, and DB fields come from the
final config.json.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.webhook import WebhookEventType
from app.services.book_update import update_book_files
from app.services.storage import UploadError

BUCKET = "publishers"
PREFIX = "edulink/books/Glory_Trio_3/"


@dataclass
class _Obj:
    object_name: str
    etag: str
    size: int


class _Response:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        pass

    def release_conn(self) -> None:
        pass


class FakeS3:
    """Just enough of the Minio client for the update path."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)
        self.puts: list[str] = []
        self.removes: list[str] = []

    def list_objects(self, bucket, prefix="", recursive=False):
        return [
            _Obj(key, f'"{hashlib.md5(data).hexdigest()}"', len(data))
            for key, data in sorted(self.objects.items())
            if key.startswith(prefix)
        ]

    def get_object(self, bucket, key):
        return _Response(self.objects[key])

    def put_object(self, bucket, key, stream, length, content_type=None):
        self.objects[key] = stream.read()
        self.puts.append(key)

    def remove_object(self, bucket, key):
        del self.objects[key]
        self.removes.append(key)


def _config(**extra) -> bytes:
    config = {
        "book_title": "Glory Trio 3",
        "book_cover": "./books/Glory_Trio_3/images/cover.png",
        "pages": [
            {"image": "./books/Glory_Trio_3/images/p1.png", "activity": {"type": "quiz"}},
            {"image": "./books/Glory_Trio_3/images/p2.png"},
        ],
    }
    config.update(extra)
    return json.dumps(config, ensure_ascii=False, indent=4).encode()


def _stored_book() -> dict[str, bytes]:
    return {
        f"{PREFIX}config.json": _config(),
        f"{PREFIX}images/cover.png": b"cover",
        f"{PREFIX}images/p1.png": b"p1",
        f"{PREFIX}images/p2.png": b"p2",
        f"{PREFIX}raw/original.pdf": b"pdf-v1",
        f"{PREFIX}audio/old.mp3": b"old",
        f"{PREFIX}ai-data/metadata.json": b"{}",
        f"{PREFIX}ai-data/modules/1.json": b"{}",
        f"{PREFIX}ai-content/quiz/1.json": b"{}",
        f"{PREFIX}additional-resources/Workbook/config.json": b"{}",
    }


def _zip(tmp_path, files: dict[str, bytes | str], name: str = "book.zip") -> str:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for entry, data in files.items():
            archive.writestr(entry, data)
    return str(path)


def _run(s3: FakeS3, archive: str, **kwargs):
    return update_book_files(
        client=s3, bucket=BUCKET, prefix=PREFIX, book_name="Glory_Trio_3", archive_path=archive, **kwargs
    )


def _protected(s3: FakeS3) -> dict[str, bytes]:
    return {k: v for k, v in s3.objects.items() if "/ai-" in k or "/additional-resources/" in k}


class TestWrites:
    def test_dry_run_writes_nothing(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/config.json": _config(), "Glory_Trio_3/games.json": "{}"})

        result = _run(s3, archive)

        assert s3.puts == [] and s3.removes == []
        assert result.written == ["games.json"]
        assert result.unchanged == ["config.json"]

    def test_partial_update_adds_files_and_keeps_everything_else(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        before = dict(s3.objects)
        archive = _zip(
            tmp_path,
            {"Glory_Trio_3/games.json": "{}", "Glory_Trio_3/assets/game1/index.html": "<html>"},
        )

        result = _run(s3, archive, dry_run=False)

        assert sorted(s3.puts) == [f"{PREFIX}assets/game1/index.html", f"{PREFIX}games.json"]
        assert s3.removes == []
        for key, data in before.items():
            assert s3.objects[key] == data
        assert result.ai_stale is False
        assert "audio/old.mp3" in result.prune_candidates and result.pruned == []

    def test_unchanged_files_are_not_resent(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(
            tmp_path,
            {"Glory_Trio_3/config.json": _config(), "Glory_Trio_3/images/p1.png": b"p1", "Glory_Trio_3/images/p2.png": b"NEW"},
        )

        result = _run(s3, archive, dry_run=False)

        assert s3.puts == [f"{PREFIX}images/p2.png"]
        assert sorted(result.unchanged) == ["config.json", "images/p1.png"]

    def test_normalized_names_and_config_paths_follow_the_upload_rules(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        config = _config(extra_audio="./books/Glory Trio 3/audio/Pg 6.MP3")
        archive = _zip(
            tmp_path, {"Glory Trio 3/config.json": config, "Glory Trio 3/audio/Pg 6.MP3": b"a"}
        )

        result = _run(s3, archive, dry_run=False)

        assert {"from": "audio/Pg 6.MP3", "to": "audio/Pg_6.mp3"} in result.renamed
        stored = json.loads(s3.objects[f"{PREFIX}config.json"])
        assert stored["extra_audio"] == "./books/Glory_Trio_3/audio/Pg_6.mp3"

    def test_partial_config_resolves_references_to_stored_files(self, tmp_path) -> None:
        """config.json points at a file the archive does not carry but storage has."""
        objects = _stored_book()
        objects[f"{PREFIX}audio/Schildkroete/1.mp3"] = b"a"
        s3 = FakeS3(objects)
        config = _config(track="./books/Glory_Trio_3/audio/Schildkröte/1.mp3")
        archive = _zip(tmp_path, {"Glory_Trio_3/config.json": config})

        _run(s3, archive, dry_run=False)

        stored = json.loads(s3.objects[f"{PREFIX}config.json"])
        assert stored["track"] == "./books/Glory_Trio_3/audio/Schildkroete/1.mp3"

    def test_images_referenced_only_by_the_stored_config_are_kept(self, tmp_path) -> None:
        """The unreferenced-image filter must see the stored config.json too."""
        s3 = FakeS3(_stored_book())
        archive = _zip(
            tmp_path, {"Glory_Trio_3/games.json": "{}", "Glory_Trio_3/images/p2.png": b"p2-new"}
        )

        result = _run(s3, archive, dry_run=False)

        assert "images/p2.png" in result.written

    def test_partial_archive_whose_root_is_a_book_folder_is_not_stripped(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"audio/new.mp3": b"n"})

        _run(s3, archive, dry_run=False)

        assert s3.puts == [f"{PREFIX}audio/new.mp3"]


class TestProtectedFolders:
    @pytest.mark.parametrize(
        "entry",
        [
            "Glory_Trio_3/ai-data/metadata.json",
            "Glory_Trio_3/AI-Content/quiz/1.json",
            "Glory_Trio_3/additional-resources/Workbook/config.json",
            "Glory_Trio_3/../other/config.json",
        ],
    )
    def test_archive_may_not_write_protected_or_escaping_paths(self, tmp_path, entry) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/config.json": _config(), entry: "x"})

        with pytest.raises(UploadError):
            _run(s3, archive, dry_run=False)
        assert s3.puts == [] and s3.removes == []

    def test_prune_never_touches_protected_folders(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        protected = _protected(s3)
        archive = _zip(
            tmp_path,
            {
                "Glory_Trio_3/config.json": _config(),
                "Glory_Trio_3/images/cover.png": b"cover",
                "Glory_Trio_3/images/p1.png": b"p1",
                "Glory_Trio_3/images/p2.png": b"p2",
                "Glory_Trio_3/raw/original.pdf": b"pdf-v1",
            },
        )

        result = _run(s3, archive, prune=True, dry_run=False)

        assert result.pruned == ["audio/old.mp3"]
        assert _protected(s3) == protected
        assert result.protected_kept == len(protected)


class TestPrune:
    def test_prune_dry_run_only_lists(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/config.json": _config()})

        result = _run(s3, archive, prune=True, dry_run=True)

        assert s3.removes == []
        assert "audio/old.mp3" in result.prune_candidates
        assert result.pruned == []
        # raw/original.pdf would go, so the AI data would be stale
        assert result.ai_stale is True

    def test_prune_needs_a_full_archive(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/games.json": "{}", "Glory_Trio_3/assets/a.js": "x"})

        with pytest.raises(UploadError, match="full book archive"):
            _run(s3, archive, prune=True, dry_run=False)
        assert s3.removes == []


class TestAiStaleAndMetadata:
    def test_same_pdf_is_not_stale(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/raw/original.pdf": b"pdf-v1", "Glory_Trio_3/games.json": "{}"})

        assert _run(s3, archive, dry_run=False).ai_stale is False

    def test_new_pdf_is_stale(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/raw/original.pdf": b"pdf-v2", "Glory_Trio_3/games.json": "{}"})

        assert _run(s3, archive, dry_run=True).ai_stale is True

    def test_metadata_comes_from_the_final_config(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        config = _config(extra=[{"activity": {"type": "matching"}}, {"activity": {"type": "quiz"}}])
        archive = _zip(tmp_path, {"Glory_Trio_3/config.json": config})

        result = _run(s3, archive, dry_run=True)

        assert result.metadata["activity_count"] == 3
        assert result.metadata["activity_details"] == {"quiz": 2, "matching": 1}
        assert result.metadata["book_cover"] == "cover.png"

    def test_metadata_falls_back_to_the_stored_config(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/games.json": "{}"})

        result = _run(s3, archive, dry_run=True)

        assert result.metadata["activity_count"] == 1

    def test_total_size_counts_book_content_only(self, tmp_path) -> None:
        s3 = FakeS3(_stored_book())
        archive = _zip(tmp_path, {"Glory_Trio_3/games.json": "{}"})

        result = _run(s3, archive, dry_run=True)

        content = sum(len(v) for k, v in _stored_book().items() if k not in _protected(s3))
        assert result.metadata["total_size"] == content + 2


# ---------------------------------------------------------------------------
# Orchestration: DB fields, content_version, webhook, bundles
# ---------------------------------------------------------------------------


def _db_book():
    return SimpleNamespace(
        id=349,
        book_type="standard",
        r2_prefix=PREFIX,
        book_name="Glory_Trio_3",
        publisher_id=7,
        publisher_rel=SimpleNamespace(slug="edulink"),
        activity_count=1,
        activity_details={"quiz": 1},
        book_cover="cover.png",
        total_size=1,
        content_version=4,
        status="published",
        ai_processing_status="completed",
    )


@pytest.fixture
def orchestration(tmp_path):
    s3 = FakeS3(_stored_book())
    book = _db_book()
    repo = MagicMock()
    repo.get_by_id.return_value = book
    session = MagicMock()
    session.__enter__.return_value = session
    with (
        patch("app.routers.books._book_repository", repo),
        patch("app.routers.books.SessionLocal", return_value=session),
        patch("app.routers.books.get_minio_client", return_value=s3),
        patch("app.routers.books._bump_content_version") as bump,
        patch("app.routers.books._invalidate_book_cache") as invalidate,
    ):
        yield SimpleNamespace(s3=s3, book=book, repo=repo, bump=bump, invalidate=invalidate, tmp_path=tmp_path)


def test_dry_run_leaves_db_and_consumers_alone(orchestration) -> None:
    from app.routers.books import run_book_files_update

    archive = _zip(orchestration.tmp_path, {"Glory_Trio_3/games.json": "{}"})
    scheduled = []

    report = run_book_files_update(
        349, archive, dry_run=True, bump_version=True, notify=True, regenerate_bundles=True,
        schedule=lambda fn, **kw: scheduled.append(fn),
    )

    orchestration.repo.update.assert_not_called()
    orchestration.bump.assert_not_called()
    orchestration.invalidate.assert_not_called()
    assert scheduled == []
    assert report["metadata"]["changed"] == ["total_size"]
    assert report["version_bumped"] is False and report["notified"] is False


def test_apply_updates_only_derived_fields_and_is_quiet_by_default(orchestration) -> None:
    from app.routers.books import run_book_files_update

    archive = _zip(orchestration.tmp_path, {"Glory_Trio_3/games.json": "{}"})
    scheduled = []

    report = run_book_files_update(349, archive, dry_run=False, schedule=lambda fn, **kw: scheduled.append(fn))

    (_session, _book), kwargs = orchestration.repo.update.call_args
    assert set(kwargs["data"]) == {"total_size"}
    orchestration.bump.assert_not_called()
    orchestration.invalidate.assert_called_once()
    assert scheduled == []
    assert report["ai_stale"] is False
    assert orchestration.book.status == "published"
    assert orchestration.book.ai_processing_status == "completed"


def test_apply_with_bump_notify_and_bundles(orchestration) -> None:
    from app.routers.books import _trigger_auto_bundles, _trigger_webhook, run_book_files_update

    archive = _zip(orchestration.tmp_path, {"Glory_Trio_3/games.json": "{}"})
    scheduled = []

    report = run_book_files_update(
        349, archive, dry_run=False, bump_version=True, notify=True, regenerate_bundles=True,
        schedule=lambda fn, **kw: scheduled.append((fn, kw)),
    )

    orchestration.bump.assert_called_once_with(349)
    assert scheduled[0] == (_trigger_webhook, {"book_id": 349, "event_type": WebhookEventType.BOOK_UPDATED})
    assert scheduled[1][0] is _trigger_auto_bundles
    assert report["version_bumped"] is True and report["notified"] is True


def test_pdf_books_are_refused(orchestration) -> None:
    from app.routers.books import run_book_files_update

    orchestration.book.book_type = "pdf"
    archive = _zip(orchestration.tmp_path, {"x.pdf": "x"})

    with pytest.raises(UploadError):
        run_book_files_update(349, archive, dry_run=False)


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_db():
    from app.db import get_db

    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db
    yield db
    app.dependency_overrides.pop(get_db, None)


@patch("app.routers.books.run_book_files_update")
@patch("app.routers.books._book_repository")
@patch("app.routers.books._require_admin", return_value=1)
def test_endpoint_defaults_to_a_safe_dry_run(_auth, repo, run, fake_db) -> None:
    repo.get_by_id.return_value = _db_book()
    run.return_value = {"ok": True}
    client = TestClient(app)

    response = client.post(
        "/books/349/update-files",
        files={"file": ("book.zip", io.BytesIO(b"PK"), "application/zip")},
        headers={"Authorization": "Bearer x"},
    )

    assert response.status_code == 200
    kwargs = run.call_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["prune"] is False
    assert kwargs["bump_version"] is False
    assert kwargs["notify"] is False
    assert kwargs["regenerate_bundles"] is False


@patch("app.routers.books.run_book_files_update", side_effect=UploadError("nope"))
@patch("app.routers.books._book_repository")
@patch("app.routers.books._require_admin", return_value=1)
def test_endpoint_maps_upload_errors_to_400(_auth, repo, _run, fake_db) -> None:
    repo.get_by_id.return_value = _db_book()
    client = TestClient(app)

    response = client.post(
        "/books/349/update-files?dry_run=false",
        files={"file": ("book.zip", io.BytesIO(b"PK"), "application/zip")},
        headers={"Authorization": "Bearer x"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "nope"


@patch("app.routers.books._trigger_webhook")
@patch("app.routers.books._invalidate_book_cache")
@patch("app.routers.books._book_repository")
@patch("app.routers.books._require_admin", return_value=1)
def test_bump_endpoint(_auth, repo, _invalidate, trigger, fake_db) -> None:
    book = MagicMock()
    book.content_version = 5
    repo.get_by_id.return_value = book
    client = TestClient(app)

    response = client.post("/books/349/content-version/bump?notify=true", headers={"Authorization": "Bearer x"})

    assert response.status_code == 200
    assert response.json() == {"book_id": 349, "content_version": 5, "notified": True}
    repo.bump_content_version.assert_called_once()
    trigger.assert_called_once_with(349, WebhookEventType.BOOK_UPDATED)


# ---------------------------------------------------------------------------
# Webhook payload
# ---------------------------------------------------------------------------


def test_webhook_payload_carries_content_version() -> None:
    from app.schemas.webhook import WebhookEventBookData

    data = WebhookEventBookData(
        id=1, book_name="b", book_title="t", publisher="p", language="en", category="", status="published",
        content_version=6,
    )
    assert json.loads(data.model_dump_json())["content_version"] == 6
    # Additive: older construction without it still validates.
    legacy = WebhookEventBookData(
        id=1, book_name="b", book_title="t", publisher="p", language="en", category="", status="published"
    )
    assert legacy.content_version is None
