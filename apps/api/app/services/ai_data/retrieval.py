"""AI data retrieval service for accessing processed book data."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from minio.error import S3Error

from app.core.config import get_settings
from app.services.ai_data.models import ProcessingMetadata
from app.services.ai_data.service import get_ai_data_metadata_service
from app.services.minio import get_minio_client, get_minio_client_external
from app.services.segmentation.storage import get_module_storage
from app.services.vocabulary_extraction.storage import get_vocabulary_storage

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class AIDataRetrievalService:
    """
    Service for retrieving AI-processed book data.

    Provides a unified interface for accessing:
    - Processing metadata
    - Module data (list and detail)
    - Vocabulary data
    - Audio file URLs
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize retrieval service.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()
        self._metadata_service = get_ai_data_metadata_service()
        self._module_storage = get_module_storage()
        self._vocabulary_storage = get_vocabulary_storage()

    def get_metadata(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> ProcessingMetadata | None:
        """
        Get processing metadata for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            ProcessingMetadata instance or None if not found.
        """
        return self._metadata_service.get_metadata(publisher_id, book_id, book_name)

    def list_modules(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> list[dict[str, Any]] | None:
        """
        List all modules for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            List of module summary dictionaries, or None if not found.
        """
        try:
            modules = self._module_storage.list_modules(publisher_id, book_id, book_name)
            if not modules:
                return None
            return modules
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            logger.error("Failed to list modules: %s", e)
            raise

    def get_module(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        module_id: int,
    ) -> dict[str, Any] | None:
        """
        Get full data for a single module.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            module_id: Module identifier.

        Returns:
            Module dictionary or None if not found.
        """
        return self._module_storage.get_module(publisher_id, book_id, book_name, module_id)

    def get_vocabulary(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        module_id: int | None = None,
    ) -> dict[str, Any] | None:
        """
        Get vocabulary data for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            module_id: Optional module ID to filter by.

        Returns:
            Vocabulary dictionary or None if not found.
        """
        vocabulary = self._vocabulary_storage.load_vocabulary(publisher_id, book_id, book_name)
        if vocabulary is None:
            return None

        # Filter by module if specified
        if module_id is not None and "words" in vocabulary:
            vocabulary["words"] = [word for word in vocabulary["words"] if word.get("module_id") == module_id]
            vocabulary["total_words"] = len(vocabulary["words"])

        return vocabulary

    def get_audio_url(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        language: str,
        word: str,
        expires_in: int = 3600,
    ) -> str | None:
        """
        Get presigned URL for a vocabulary audio file.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            language: Language code (e.g., 'en', 'tr').
            word: The vocabulary word (used as filename).
            expires_in: URL expiration time in seconds (default: 1 hour).

        Returns:
            Presigned URL string or None if file not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Build audio file path
        # Path: {publisher_id}/books/{book_name}/ai-data/audio/vocabulary/{lang}/{word}.mp3
        audio_path = f"{publisher_id}/books/{book_name}/ai-data/audio/vocabulary/{language}/{word}.mp3"

        # Check if file exists
        try:
            client.stat_object(bucket, audio_path)
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            logger.error("Failed to check audio file: %s", e)
            raise

        # Generate presigned URL using external client (for browser access)
        # The signature includes the host, so we must use the external endpoint
        external_client = get_minio_client_external(self.settings)
        try:
            presigned_url = external_client.presigned_get_object(
                bucket_name=bucket,
                object_name=audio_path,
                expires=timedelta(seconds=expires_in),
            )
            return presigned_url
        except S3Error as e:
            logger.error("Failed to generate presigned URL: %s", e)
            raise

    def audio_exists(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        language: str,
        word: str,
    ) -> bool:
        """
        Check if an audio file exists.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            language: Language code.
            word: The vocabulary word.

        Returns:
            True if audio file exists.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        audio_path = f"{publisher_id}/books/{book_name}/ai-data/audio/vocabulary/{language}/{word}.mp3"

        try:
            client.stat_object(bucket, audio_path)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise

    def get_modules_metadata(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> dict[str, Any] | None:
        """
        Get modules metadata.json containing summary info for all modules.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Modules metadata dictionary or None if not found.
        """
        import json

        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Path: {publisher_id}/books/{book_name}/ai-data/modules/metadata.json
        metadata_path = f"{publisher_id}/books/{book_name}/ai-data/modules/metadata.json"

        try:
            response = client.get_object(bucket, metadata_path)
            data = response.read()
            response.close()
            response.release_conn()
            return json.loads(data.decode("utf-8"))
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            logger.error("Failed to get modules metadata: %s", e)
            raise


# Singleton instance
_retrieval_service: AIDataRetrievalService | None = None


def get_ai_data_retrieval_service() -> AIDataRetrievalService:
    """Get or create the global AI data retrieval service instance."""
    global _retrieval_service
    if _retrieval_service is None:
        _retrieval_service = AIDataRetrievalService()
    return _retrieval_service
