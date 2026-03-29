"""AI data metadata service for managing processing metadata.json."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any

from minio.error import S3Error

from app.core.config import get_settings
from app.services.ai_data.models import (
    MetadataError,
    ProcessingMetadata,
    ProcessingStatus,
    StageResult,
    StageStatus,
)
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class AIDataMetadataService:
    """
    Service for managing AI processing metadata.

    Handles creation, updates, and retrieval of the consolidated
    metadata.json file at ai-data/metadata.json.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize metadata service.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def _build_metadata_path(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build path for metadata.json file."""
        # Path: {publisher_id}/books/{book_name}/ai-data (book_id not in path)
        return f"{publisher_id}/books/{book_name}/ai-data/metadata.json"

    def create_metadata(
        self,
        book_id: str,
        publisher_id: int,
        book_name: str,
    ) -> ProcessingMetadata:
        """
        Create initial metadata.json with processing status.

        Args:
            book_id: Book identifier.
            publisher_id: Publisher ID (integer).
            book_name: Book folder name.

        Returns:
            Created ProcessingMetadata instance.

        Raises:
            MetadataError: If creation fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Create initial metadata
        metadata = ProcessingMetadata(
            book_id=book_id,
            publisher_id=publisher_id,
            book_name=book_name,
            processing_status=ProcessingStatus.PROCESSING,
            processing_started_at=datetime.now(timezone.utc),
            llm_provider=self.settings.llm_primary_provider,
            tts_provider=self.settings.tts_primary_provider,
            stages={
                "text_extraction": StageResult(status=StageStatus.PENDING),
                "segmentation": StageResult(status=StageStatus.PENDING),
                "topic_analysis": StageResult(status=StageStatus.PENDING),
                "vocabulary": StageResult(status=StageStatus.PENDING),
                "audio_generation": StageResult(status=StageStatus.PENDING),
                "chunked_analysis": StageResult(status=StageStatus.PENDING),
            },
        )

        path = self._build_metadata_path(publisher_id, book_id, book_name)

        try:
            self._save_metadata(client, bucket, path, metadata)
            logger.info(
                "Created metadata.json for book %s: %s",
                book_id,
                path,
            )
            return metadata
        except S3Error as e:
            logger.error("Failed to create metadata %s: %s", path, e)
            raise MetadataError(book_id, "create", str(e))

    def update_metadata(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        stage_name: str,
        stage_result: dict[str, Any],
        success: bool = True,
        error_message: str = "",
    ) -> ProcessingMetadata:
        """
        Update metadata.json with stage results.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            stage_name: Name of the completed stage.
            stage_result: Dictionary of stage-specific results.
            success: Whether the stage succeeded.
            error_message: Error message if failed.

        Returns:
            Updated ProcessingMetadata instance.

        Raises:
            MetadataError: If update fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket
        path = self._build_metadata_path(publisher_id, book_id, book_name)

        # Load existing metadata
        metadata = self.get_metadata(publisher_id, book_id, book_name)
        if metadata is None:
            # Create if doesn't exist
            metadata = self.create_metadata(book_id, publisher_id, book_name)

        # Update stage result
        stage = StageResult(
            status=StageStatus.COMPLETED if success else StageStatus.FAILED,
            completed_at=datetime.now(timezone.utc),
            error_message=error_message,
            data=stage_result,
        )
        metadata.stages[stage_name] = stage

        # Update aggregated fields based on stage
        self._update_aggregated_fields(metadata, stage_name, stage_result)

        # Add error if stage failed
        if not success and error_message:
            metadata.errors.append(
                {
                    "stage": stage_name,
                    "error": error_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        try:
            self._save_metadata(client, bucket, path, metadata)
            logger.info(
                "Updated metadata.json stage %s for book %s",
                stage_name,
                book_id,
            )
            return metadata
        except S3Error as e:
            logger.error("Failed to update metadata %s: %s", path, e)
            raise MetadataError(book_id, "update", str(e))

    def _update_aggregated_fields(
        self,
        metadata: ProcessingMetadata,
        stage_name: str,
        stage_result: dict[str, Any],
    ) -> None:
        """Update aggregated fields based on stage results."""
        if stage_name == "text_extraction":
            metadata.total_pages = stage_result.get("total_pages", 0)

        elif stage_name == "segmentation":
            metadata.total_modules = stage_result.get("module_count", 0)

        elif stage_name == "topic_analysis":
            primary_lang = stage_result.get("primary_language", "")
            if primary_lang:
                metadata.primary_language = primary_lang
                if primary_lang not in metadata.languages:
                    metadata.languages.append(primary_lang)
            difficulty = stage_result.get("difficulty_range", [])
            if difficulty:
                metadata.difficulty_range = difficulty

        elif stage_name == "vocabulary":
            metadata.total_vocabulary = stage_result.get("total_words", 0)
            translation_lang = stage_result.get("translation_language", "")
            if translation_lang and translation_lang not in metadata.languages:
                metadata.languages.append(translation_lang)

        elif stage_name == "audio_generation":
            metadata.total_audio_files = stage_result.get("audio_files_saved", 0)

        elif stage_name in ("chunked_analysis", "unified_analysis"):
            # Unified/chunked analysis provides modules, vocabulary, language, and difficulty
            metadata.total_modules = stage_result.get("module_count", 0)
            metadata.total_vocabulary = stage_result.get("total_vocabulary", 0)
            primary_lang = stage_result.get("primary_language", "")
            if primary_lang:
                metadata.primary_language = primary_lang
                if primary_lang not in metadata.languages:
                    metadata.languages.append(primary_lang)
            translation_lang = stage_result.get("translation_language", "")
            if translation_lang and translation_lang not in metadata.languages:
                metadata.languages.append(translation_lang)
            difficulty = stage_result.get("difficulty_range", [])
            if difficulty:
                metadata.difficulty_range = difficulty

    def finalize_metadata(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        final_status: ProcessingStatus,
        error_message: str = "",
    ) -> ProcessingMetadata:
        """
        Finalize metadata.json with final status and completion timestamp.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            final_status: Final processing status.
            error_message: Error message if failed.

        Returns:
            Finalized ProcessingMetadata instance.

        Raises:
            MetadataError: If finalization fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket
        path = self._build_metadata_path(publisher_id, book_id, book_name)

        # Load existing metadata
        metadata = self.get_metadata(publisher_id, book_id, book_name)
        if metadata is None:
            raise MetadataError(book_id, "finalize", "Metadata not found")

        # Update final status
        metadata.processing_status = final_status
        metadata.processing_completed_at = datetime.now(timezone.utc)

        if error_message:
            metadata.errors.append(
                {
                    "stage": "finalization",
                    "error": error_message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        try:
            self._save_metadata(client, bucket, path, metadata)
            logger.info(
                "Finalized metadata.json with status %s for book %s",
                final_status.value,
                book_id,
            )
            return metadata
        except S3Error as e:
            logger.error("Failed to finalize metadata %s: %s", path, e)
            raise MetadataError(book_id, "finalize", str(e))

    def get_metadata(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> ProcessingMetadata | None:
        """
        Retrieve current metadata.json.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            ProcessingMetadata instance or None if not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket
        path = self._build_metadata_path(publisher_id, book_id, book_name)

        try:
            response = client.get_object(bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            metadata_dict = json.loads(data.decode("utf-8"))
            return ProcessingMetadata.from_dict(metadata_dict)
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            logger.error("Failed to get metadata %s: %s", path, e)
            raise MetadataError(book_id, "get", str(e))

    def metadata_exists(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if metadata.json exists.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            True if metadata exists.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket
        path = self._build_metadata_path(publisher_id, book_id, book_name)

        try:
            client.stat_object(bucket, path)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise

    def _save_metadata(
        self,
        client: Any,
        bucket: str,
        path: str,
        metadata: ProcessingMetadata,
    ) -> None:
        """Save metadata to MinIO."""
        json_str = json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False)
        json_bytes = json_str.encode("utf-8")
        data = BytesIO(json_bytes)

        client.put_object(
            bucket,
            path,
            data,
            length=len(json_bytes),
            content_type="application/json; charset=utf-8",
        )


# Singleton instance
_metadata_service: AIDataMetadataService | None = None


def get_ai_data_metadata_service() -> AIDataMetadataService:
    """Get or create the global AI data metadata service instance."""
    global _metadata_service
    if _metadata_service is None:
        _metadata_service = AIDataMetadataService()
    return _metadata_service
