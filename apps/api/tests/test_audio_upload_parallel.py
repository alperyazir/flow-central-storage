"""Audio files go to storage in parallel, and off the worker's event loop.

Measured on 2026-09-22: 80 files took 9s to synthesise and 29s to upload, one
PUT at a time (~0.35s each). A book with 1400 words spent seven minutes there —
and because the upload ran inline in an async task, the other two books sharing
the worker's event loop were frozen for every one of those minutes.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from app.services.audio_generation.models import AudioFile
from app.services.audio_generation.storage import AudioStorage


def _audio_files(count: int) -> tuple[list[AudioFile], dict[str, bytes]]:
    files = [
        AudioFile(
            word_id=f"w{i}",
            word=f"word{i}",
            language="en",
            file_path=f"audio/vocabulary/en/w{i}.mp3",
        )
        for i in range(count)
    ]
    return files, {f.file_path: b"mp3" for f in files}


@pytest.fixture
def storage() -> AudioStorage:
    settings = MagicMock()
    settings.minio_publishers_bucket = "flow-publishers"
    settings.audio_upload_concurrency = 8
    return AudioStorage(settings=settings)


class TestParallelUpload:
    @patch("app.services.audio_generation.storage.get_minio_client")
    def test_uploads_overlap(self, mock_get_client: MagicMock, storage: AudioStorage) -> None:
        """The point of the change: PUTs are in flight at the same time."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        in_flight = 0
        peak = 0
        lock = threading.Lock()
        barrier = threading.Barrier(8, timeout=5)

        def slow_put(*args: object, **kwargs: object) -> None:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            # Blocks until 8 threads arrive, so a serial loop would time out.
            barrier.wait()
            with lock:
                in_flight -= 1

        mock_client.put_object.side_effect = slow_put
        files, data = _audio_files(24)

        result = storage.save_all_audio(
            publisher_slug="universal-elt",
            book_id="285",
            book_name="Harvest_Practice_Tests_1",
            audio_files=files,
            audio_data=data,
        )

        assert result["saved"] == 24
        assert peak == 8

    @patch("app.services.audio_generation.storage.get_minio_client")
    def test_every_file_is_saved_and_counted(
        self, mock_get_client: MagicMock, storage: AudioStorage
    ) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        files, data = _audio_files(50)

        result = storage.save_all_audio(
            publisher_slug="universal-elt",
            book_id="285",
            book_name="B",
            audio_files=files,
            audio_data=data,
        )

        assert result["saved"] == 50
        assert result["failed"] == 0
        assert len(set(result["paths"])) == 50
        assert mock_client.put_object.call_count == 50

    @patch("app.services.audio_generation.storage.get_minio_client")
    def test_a_missing_or_failing_file_is_counted_not_raised(
        self, mock_get_client: MagicMock, storage: AudioStorage
    ) -> None:
        """One bad word must not cost the book its other 49."""
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        files, data = _audio_files(3)
        del data[files[0].file_path]  # never synthesised

        def put(*args: object, **kwargs: object) -> None:
            if args[1].endswith("w1.mp3"):
                raise S3Error("XError", "boom", args[1], "rid", "hid", None)

        mock_client.put_object.side_effect = put

        result = storage.save_all_audio(
            publisher_slug="universal-elt",
            book_id="285",
            book_name="B",
            audio_files=files,
            audio_data=data,
        )

        assert result["saved"] == 1
        assert result["failed"] == 2

    def test_concurrency_falls_back_when_unconfigured(self) -> None:
        settings = MagicMock()
        settings.audio_upload_concurrency = None
        assert AudioStorage(settings=settings).upload_concurrency == 16


class TestParallelCleanup:
    @patch("app.services.audio_generation.storage.get_minio_client")
    def test_deletes_overlap_and_are_counted(
        self, mock_get_client: MagicMock, storage: AudioStorage
    ) -> None:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.list_objects.return_value = [
            MagicMock(object_name=f"universal-elt/books/B/ai-data/audio/vocabulary/en/w{i}.mp3")
            for i in range(24)
        ]

        peak = 0
        in_flight = 0
        lock = threading.Lock()
        barrier = threading.Barrier(8, timeout=5)

        def slow_remove(*args: object, **kwargs: object) -> None:
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            barrier.wait()
            with lock:
                in_flight -= 1

        mock_client.remove_object.side_effect = slow_remove

        deleted = storage.cleanup_audio_directory("universal-elt", "285", "B")

        assert deleted == 24
        assert peak == 8


class TestOffTheEventLoop:
    """The task must hand these blocking calls to a thread."""

    @pytest.mark.asyncio
    async def test_storage_calls_run_in_threads(self) -> None:
        import inspect
        import re

        from app.services.queue import tasks

        source = inspect.getsource(tasks._run_audio_generation)
        for call in ("cleanup_audio_directory", "save_all_audio", "update_vocabulary_audio_paths"):
            handed_to_a_thread = re.search(
                rf"asyncio\.to_thread\(\s*audio_storage\.{call}\b", source
            )
            assert handed_to_a_thread, f"{call} still blocks the event loop"
            called_inline = re.search(rf"(?<!\.)\baudio_storage\.{call}\(", source)
            assert not called_inline, f"{call} is still called directly"
