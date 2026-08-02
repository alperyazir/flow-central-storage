"""Unit tests for the AI-status helpers behind sync-r2 and override uploads."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.routers.books import _ai_status_from_storage, _reset_ai_status_for_prefix


class TestAiStatusFromStorage:
    """ai-data/metadata.json is the source of truth for a rebuilt DB."""

    @patch("app.routers.books.get_ai_data_retrieval_service")
    def test_reads_status_and_timestamp(self, mock_service: MagicMock) -> None:
        completed_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        metadata = MagicMock()
        metadata.processing_status.value = "completed"
        metadata.processing_completed_at = completed_at
        mock_service.return_value.get_metadata.return_value = metadata

        data = _ai_status_from_storage("edulink", "Glory_Trio_8", 145)

        assert data == {
            "ai_processing_status": "completed",
            "ai_processed_at": completed_at,
        }
        # The lookup is keyed by slug + book name; the id is incidental.
        args = mock_service.return_value.get_metadata.call_args.args
        assert args[0] == "edulink"
        assert args[2] == "Glory_Trio_8"

    @patch("app.routers.books.get_ai_data_retrieval_service")
    def test_no_ai_data_yields_nothing(self, mock_service: MagicMock) -> None:
        mock_service.return_value.get_metadata.return_value = None

        assert _ai_status_from_storage("edulink", "Some_Book", 1) == {}

    @patch("app.routers.books.get_ai_data_retrieval_service")
    def test_storage_failure_does_not_raise(self, mock_service: MagicMock) -> None:
        """Sync must survive an unreadable metadata.json."""
        mock_service.return_value.get_metadata.side_effect = OSError("R2 down")

        assert _ai_status_from_storage("edulink", "Some_Book", 1) == {}

    @patch("app.routers.books.get_ai_data_retrieval_service")
    def test_omits_timestamp_when_absent(self, mock_service: MagicMock) -> None:
        metadata = MagicMock()
        metadata.processing_status.value = "partial"
        metadata.processing_completed_at = None
        mock_service.return_value.get_metadata.return_value = metadata

        assert _ai_status_from_storage("edulink", "B", 1) == {
            "ai_processing_status": "partial"
        }


class TestResetAiStatusForPrefix:
    """An override upload wipes ai-data, so the mirrored status must go too."""

    @patch("app.routers.books.clear_book_ai_status")
    @patch("app.routers.books._book_repository")
    @patch("app.routers.books._publisher_repository")
    @patch("app.routers.books.SessionLocal")
    def test_clears_the_book_behind_the_prefix(
        self,
        mock_session_local: MagicMock,
        mock_pub_repo: MagicMock,
        mock_book_repo: MagicMock,
        mock_clear: MagicMock,
    ) -> None:
        mock_session_local.return_value.__enter__.return_value = MagicMock()
        mock_pub_repo.get_by_slug.return_value = MagicMock(id=3)
        mock_book_repo.get_by_publisher_id_and_name.return_value = MagicMock(id=145)

        _reset_ai_status_for_prefix("edulink/books/Glory_Trio_8/")

        assert mock_pub_repo.get_by_slug.call_args.args[1] == "edulink"
        assert mock_book_repo.get_by_publisher_id_and_name.call_args.kwargs == {
            "publisher_id": 3,
            "book_name": "Glory_Trio_8",
        }
        mock_clear.assert_called_once_with(145)

    @patch("app.routers.books.clear_book_ai_status")
    @patch("app.routers.books.SessionLocal")
    def test_malformed_prefix_is_ignored(
        self, mock_session_local: MagicMock, mock_clear: MagicMock
    ) -> None:
        """Better to leave the status alone than clear the wrong book."""
        for prefix in ("edulink/", "edulink/assets/Glory/", ""):
            _reset_ai_status_for_prefix(prefix)

        mock_clear.assert_not_called()
        mock_session_local.assert_not_called()

    @patch("app.routers.books.clear_book_ai_status")
    @patch("app.routers.books._book_repository")
    @patch("app.routers.books._publisher_repository")
    @patch("app.routers.books.SessionLocal")
    def test_unknown_book_is_a_no_op(
        self,
        mock_session_local: MagicMock,
        mock_pub_repo: MagicMock,
        mock_book_repo: MagicMock,
        mock_clear: MagicMock,
    ) -> None:
        mock_session_local.return_value.__enter__.return_value = MagicMock()
        mock_pub_repo.get_by_slug.return_value = MagicMock(id=3)
        mock_book_repo.get_by_publisher_id_and_name.return_value = None

        _reset_ai_status_for_prefix("edulink/books/Missing_Book/")

        mock_clear.assert_not_called()

    @patch("app.routers.books.clear_book_ai_status")
    @patch("app.routers.books.SessionLocal")
    def test_db_failure_never_breaks_the_upload(
        self, mock_session_local: MagicMock, mock_clear: MagicMock
    ) -> None:
        mock_session_local.side_effect = RuntimeError("no DB")

        _reset_ai_status_for_prefix("edulink/books/Glory_Trio_8/")

        mock_clear.assert_not_called()
