"""Tests for storage service helpers."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock

import pytest
from minio.error import S3Error

from app.services.storage import (
    RelocationError,
    RestorationError,
    UploadConflictError,
    UploadError,
    ensure_version_target,
    extract_manifest_version,
    iter_zip_entries,
    list_trash_entries,
    move_prefix_to_trash,
    restore_prefix_from_trash,
    upload_book_archive,
)


@pytest.fixture()
def sample_archive_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("chapter1.txt", "Once upon a time")
        archive.writestr("chapter2.txt", "The end")
    return buffer.getvalue()


def test_iter_zip_entries_skips_directories() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("folder/", "")
        archive.writestr("folder/file.txt", "content")

    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    entries = list(iter_zip_entries(archive))
    assert len(entries) == 1
    assert entries[0][0].filename == "folder/file.txt"


def test_iter_zip_entries_filters_unwanted_files() -> None:
    """Test that .fbinf, .bak, and .tmp files are filtered out (case-insensitive)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # Files that should be filtered
        archive.writestr("content/index.fbinf", "index data")
        archive.writestr("content/backup.bak", "backup data")
        archive.writestr("content/UPPERCASE.BAK", "backup data")
        archive.writestr("content/temp.tmp", "temp data")
        archive.writestr("nested/file.FBINF", "nested index")
        archive.writestr("config.bak.old", "old backup")  # Should NOT be filtered (doesn't end with .bak)

        # Files that should pass through
        archive.writestr("content/legitimate.pdf", "pdf content")
        archive.writestr("content/data/config.json", "{}")
        archive.writestr("README.md", "# README")

    archive = zipfile.ZipFile(io.BytesIO(buffer.getvalue()))
    entries = list(iter_zip_entries(archive))

    # Extract just the filenames
    filenames = [entry.filename for entry, _ in entries]

    # Assert filtered files are NOT present
    assert "content/index.fbinf" not in filenames
    assert "content/backup.bak" not in filenames
    assert "content/UPPERCASE.BAK" not in filenames
    assert "content/temp.tmp" not in filenames
    assert "nested/file.FBINF" not in filenames

    # Assert legitimate files ARE present
    assert "content/legitimate.pdf" in filenames
    assert "content/data/config.json" in filenames
    assert "README.md" in filenames
    assert "config.bak.old" in filenames  # Edge case: .bak.old should pass through

    # Verify total count
    assert len(filenames) == 4


def test_upload_book_archive_puts_files(sample_archive_bytes: bytes) -> None:
    client = MagicMock()

    manifest = upload_book_archive(
        client=client,
        archive_bytes=sample_archive_bytes,
        bucket="publishers",
        object_prefix="dream/books/sky/",
    )

    assert len(manifest) == 2
    client.put_object.assert_any_call(
        "publishers",
        "dream/books/sky/chapter1.txt",
        ANY,
        length=len("Once upon a time"),
        content_type="application/octet-stream",
    )


def test_upload_rewrites_audio_json_references() -> None:
    """audio.json is normalized like config.json: refs match the renamed files."""
    import json

    captured: dict[str, bytes] = {}

    def _capture(bucket, path, stream, length=None, content_type=None):
        captured[path] = stream.read()

    client = MagicMock()
    client.put_object.side_effect = _capture

    audio_json = json.dumps(
        {
            "tracks": [
                "Ünite 1.MP3",  # bare reference (no directory)
                "./audio/turuncu.renk.MP3",  # path reference with dotted stem
            ]
        }
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Book/audio/Ünite 1.MP3", "a")
        archive.writestr("Book/audio/turuncu.renk.MP3", "b")
        archive.writestr("Book/audio/audio.json", audio_json)

    upload_book_archive(
        client=client,
        archive_bytes=buffer.getvalue(),
        bucket="publishers",
        object_prefix="dream/books/Book/",
        book_name="Book",
    )

    out = json.loads(captured["dream/books/Book/audio/audio.json"])
    assert out["tracks"][0] == "Unite_1.mp3"
    assert out["tracks"][1] == "./audio/turuncu_renk.mp3"
    # The renamed asset objects themselves were stored under normalized keys.
    assert "dream/books/Book/audio/Unite_1.mp3" in captured
    assert "dream/books/Book/audio/turuncu_renk.mp3" in captured


def test_upload_book_archive_raises_for_invalid_zip() -> None:
    client = MagicMock()

    with pytest.raises(UploadError):
        upload_book_archive(
            client=client,
            archive_bytes=b"not a zip",
            bucket="publishers",
            object_prefix="dream/books/sky/",
        )


def test_extract_manifest_version_returns_trimmed_value() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data/version", "  v1.2.3 \n")

    version = extract_manifest_version(buffer.getvalue())
    assert version == "v1.2.3"


def test_extract_manifest_version_requires_semver() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data/version", "one.two")

    with pytest.raises(UploadError):
        extract_manifest_version(buffer.getvalue())


def test_extract_manifest_version_requires_file() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.txt", "hi")

    with pytest.raises(UploadError):
        extract_manifest_version(buffer.getvalue())


def test_move_prefix_to_trash_relocates_objects() -> None:
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(object_name="dream/books/sky/chapter1.txt"),
        SimpleNamespace(object_name="dream/books/sky/notes/chapter2.txt"),
    ]

    report = move_prefix_to_trash(
        client=client,
        source_bucket="publishers",
        prefix="dream/books/sky",
        trash_bucket="trash",
    )

    assert report.objects_moved == 2
    copy_calls = client.copy_object.call_args_list
    assert len(copy_calls) == 2
    first_source = copy_calls[0][0][2]
    assert first_source.bucket_name == "publishers"
    assert first_source.object_name == "dream/books/sky/chapter1.txt"
    client.remove_object.assert_any_call("publishers", "dream/books/sky/chapter1.txt")
    assert report.destination_prefix == "publishers/dream/books/sky/"


def test_move_prefix_to_trash_allows_empty_prefix() -> None:
    client = MagicMock()
    client.list_objects.return_value = []

    report = move_prefix_to_trash(
        client=client,
        source_bucket="publishers",
        prefix="dream/books/sky/",
        trash_bucket="trash",
    )

    assert report.objects_moved == 0
    client.copy_object.assert_not_called()
    client.remove_object.assert_not_called()


def test_move_prefix_to_trash_raises_on_copy_failure() -> None:
    client = MagicMock()
    client.list_objects.return_value = [SimpleNamespace(object_name="dream/books/sky/file.txt")]
    client.copy_object.side_effect = S3Error(
        "InternalError",
        "copy failed",
        "dream/books/sky/file.txt",
        "request",
        "host",
        None,
    )

    with pytest.raises(RelocationError):
        move_prefix_to_trash(
            client=client,
            source_bucket="publishers",
            prefix="dream/books/sky/",
            trash_bucket="trash",
        )


def test_move_prefix_to_trash_raises_when_listing_fails() -> None:
    client = MagicMock()
    client.list_objects.side_effect = S3Error(
        "InternalError",
        "list failed",
        "dream/books/sky/",
        "request",
        "host",
        None,
    )

    with pytest.raises(RelocationError):
        move_prefix_to_trash(
            client=client,
            source_bucket="publishers",
            prefix="dream/books/sky/",
            trash_bucket="trash",
        )


def test_ensure_version_target_detects_conflict() -> None:
    client = MagicMock()
    client.list_objects.return_value = iter([SimpleNamespace(object_name="dream/books/sky/1.0.0/file.txt")])

    with pytest.raises(UploadConflictError):
        ensure_version_target(
            client=client,
            bucket="publishers",
            prefix="dream/books/sky/1.0.0/",
            version="1.0.0",
            override=False,
        )


def test_ensure_version_target_allows_override() -> None:
    client = MagicMock()
    client.list_objects.return_value = iter([SimpleNamespace(object_name="dream/books/sky/1.0.0/file.txt")])

    result = ensure_version_target(
        client=client,
        bucket="publishers",
        prefix="dream/books/sky/1.0.0/",
        version="1.0.0",
        override=True,
    )

    assert result is True


def test_restore_prefix_from_trash_restores_objects() -> None:
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(object_name="publishers/DreamPress/books/SkyTales/chapter1.txt"),
        SimpleNamespace(object_name="publishers/DreamPress/books/SkyTales/notes/chapter2.txt"),
    ]

    report = restore_prefix_from_trash(
        client=client,
        trash_bucket="trash",
        key="publishers/DreamPress/books/SkyTales/",
    )

    assert report.objects_moved == 2
    copy_calls = client.copy_object.call_args_list
    assert copy_calls[0][0][0] == "publishers"
    assert copy_calls[0][0][1] == "DreamPress/books/SkyTales/chapter1.txt"
    remove_calls = client.remove_object.call_args_list
    assert remove_calls[0][0][0] == "trash"


def test_restore_prefix_from_trash_raises_when_empty() -> None:
    client = MagicMock()
    client.list_objects.return_value = []

    with pytest.raises(RestorationError):
        restore_prefix_from_trash(
            client=client,
            trash_bucket="trash",
            key="publishers/DreamPress/books/SkyTales/",
        )


def test_list_trash_entries_aggregates_books_and_apps() -> None:
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(
            object_name="publishers/Press/books/Atlas/file1.txt",
            size=10,
            last_modified=datetime.now(UTC) - timedelta(days=8),
        ),
        SimpleNamespace(
            object_name="publishers/Press/books/Atlas/notes/file2.txt",
            size=5,
            last_modified=datetime.now(UTC) - timedelta(days=6),
        ),
        SimpleNamespace(
            object_name="apps/macos/1.0/app.zip",
            size=20,
            last_modified=datetime.now(UTC) - timedelta(days=2),
        ),
    ]

    entries = list_trash_entries(client, "trash", timedelta(days=7))

    keys = {entry.key for entry in entries}
    assert keys == {"apps/macos/1.0/", "publishers/Press/books/Atlas/"}
    book_entry = next(entry for entry in entries if entry.item_type == "book")
    assert book_entry.object_count == 2
    assert book_entry.total_size == 15
    assert book_entry.metadata == {"publisher": "Press", "book_name": "Atlas"}
    assert book_entry.youngest_last_modified is not None
    assert book_entry.eligible_at is not None
    assert book_entry.eligible_for_deletion is False

    app_entry = next(entry for entry in entries if entry.item_type == "app")
    assert app_entry.eligible_for_deletion is False


def test_list_trash_entries_aggregates_teacher_materials() -> None:
    """Test that list_trash_entries correctly aggregates teacher materials."""
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(
            object_name="teachers/teacher_123/materials/lesson.pdf",
            size=1024,
            last_modified=datetime.now(UTC) - timedelta(days=3),
        ),
        SimpleNamespace(
            object_name="teachers/teacher_123/materials/audio/intro.mp3",
            size=2048,
            last_modified=datetime.now(UTC) - timedelta(days=2),
        ),
        SimpleNamespace(
            object_name="teachers/teacher_456/materials/notes.txt",
            size=512,
            last_modified=datetime.now(UTC) - timedelta(days=10),
        ),
    ]

    entries = list_trash_entries(client, "trash", timedelta(days=7))

    # Should have 2 entries for 2 different teachers
    teacher_entries = [e for e in entries if e.item_type == "teacher_material"]
    assert len(teacher_entries) == 2

    # Verify teacher_123 entry
    teacher_123_entry = next(e for e in teacher_entries if e.metadata and e.metadata.get("teacher_id") == "teacher_123")
    assert teacher_123_entry.key == "teachers/teacher_123/materials/"
    assert teacher_123_entry.bucket == "teachers"
    assert teacher_123_entry.path == "teacher_123/materials"
    assert teacher_123_entry.object_count == 2
    assert teacher_123_entry.total_size == 1024 + 2048
    assert teacher_123_entry.eligible_for_deletion is False  # Youngest is 2 days old, retention is 7 days

    # Verify teacher_456 entry
    teacher_456_entry = next(e for e in teacher_entries if e.metadata and e.metadata.get("teacher_id") == "teacher_456")
    assert teacher_456_entry.key == "teachers/teacher_456/materials/"
    assert teacher_456_entry.object_count == 1
    assert teacher_456_entry.total_size == 512
    assert teacher_456_entry.eligible_for_deletion is True  # 10 days old, past 7 day retention


def test_move_prefix_to_trash_teacher_materials() -> None:
    """Test that teacher materials are correctly moved to trash."""
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(object_name="teacher_123/materials/lesson.pdf"),
    ]

    report = move_prefix_to_trash(
        client=client,
        source_bucket="teachers",
        prefix="teacher_123/materials/lesson.pdf",
        trash_bucket="trash",
    )

    assert report.objects_moved == 1
    assert report.source_bucket == "teachers"
    assert report.destination_bucket == "trash"
    # Verify the destination prefix includes the source bucket
    assert "teachers/teacher_123/materials/lesson.pdf" in report.destination_prefix


def test_restore_prefix_from_trash_teacher_materials() -> None:
    """Test that teacher materials are correctly restored from trash."""
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(object_name="teachers/teacher_123/materials/lesson.pdf"),
    ]

    report = restore_prefix_from_trash(
        client=client,
        trash_bucket="trash",
        key="teachers/teacher_123/materials/",
    )

    assert report.objects_moved == 1
    assert report.destination_bucket == "teachers"
    # Verify the copy was to the correct destination
    copy_calls = client.copy_object.call_args_list
    assert len(copy_calls) == 1
    assert copy_calls[0][0][0] == "teachers"  # destination bucket
    assert copy_calls[0][0][1] == "teacher_123/materials/lesson.pdf"  # destination key


def test_list_trash_entries_aggregates_publisher_assets() -> None:
    """Test that list_trash_entries correctly aggregates publisher assets."""
    client = MagicMock()
    client.list_objects.return_value = [
        SimpleNamespace(
            object_name="publishers/Dream Press/assets/materials/worksheet1.pdf",
            size=1024,
            last_modified=datetime.now(UTC) - timedelta(days=3),
        ),
        SimpleNamespace(
            object_name="publishers/Dream Press/assets/materials/worksheet2.pdf",
            size=2048,
            last_modified=datetime.now(UTC) - timedelta(days=2),
        ),
        SimpleNamespace(
            object_name="publishers/Dream Press/assets/logos/logo.png",
            size=512,
            last_modified=datetime.now(UTC) - timedelta(days=10),
        ),
    ]

    entries = list_trash_entries(client, "trash", timedelta(days=7))

    # Should have 2 entries for 2 different asset types
    asset_entries = [e for e in entries if e.item_type == "publisher_asset"]
    assert len(asset_entries) == 2

    # Verify materials entry
    materials_entry = next(e for e in asset_entries if e.metadata and e.metadata.get("asset_type") == "materials")
    assert materials_entry.key == "publishers/Dream Press/assets/materials/"
    assert materials_entry.bucket == "publishers"
    assert materials_entry.path == "Dream Press/assets/materials"
    assert materials_entry.object_count == 2
    assert materials_entry.total_size == 1024 + 2048
    assert materials_entry.metadata["publisher"] == "Dream Press"
    assert materials_entry.eligible_for_deletion is False  # Youngest is 2 days old, retention is 7 days

    # Verify logos entry
    logos_entry = next(e for e in asset_entries if e.metadata and e.metadata.get("asset_type") == "logos")
    assert logos_entry.key == "publishers/Dream Press/assets/logos/"
    assert logos_entry.object_count == 1
    assert logos_entry.total_size == 512
    assert logos_entry.metadata["publisher"] == "Dream Press"
    assert logos_entry.eligible_for_deletion is True  # 10 days old, past 7 day retention
