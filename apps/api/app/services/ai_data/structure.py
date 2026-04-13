"""AI data directory structure manager."""

from __future__ import annotations

import logging
from io import BytesIO
from typing import TYPE_CHECKING

from minio.error import S3Error

from app.core.config import get_settings
from app.services.ai_data.models import (
    AIDataStructure,
    InitializationError,
)
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class AIDataStructureManager:
    """
    Manager for AI data directory structure.

    Handles initialization and verification of the ai-data directory
    structure in MinIO storage.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize structure manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def get_ai_data_paths(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> AIDataStructure:
        """
        Get all expected paths for ai-data structure.

        Args:
            publisher_slug: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            AIDataStructure with all paths.
        """
        return AIDataStructure.from_book_info(publisher_slug, book_id, book_name)

    def initialize_ai_data_structure(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> AIDataStructure:
        """
        Ensure all ai-data subdirectories exist.

        In MinIO/S3, directories don't technically exist - they're implied
        by object paths. This method creates placeholder objects to ensure
        the directory structure is visible in storage browsers.

        Args:
            publisher_slug: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            AIDataStructure with all paths.

        Raises:
            InitializationError: If initialization fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self.get_ai_data_paths(publisher_slug, book_id, book_name)

        logger.info(
            "Initializing ai-data structure for book %s at %s",
            book_id,
            structure.base_path,
        )

        # Create placeholder files for each directory
        # This makes directories visible in S3 browsers
        directories = structure.get_all_directories()

        for dir_path in directories:
            placeholder_path = f"{dir_path}/.keep"
            try:
                # Check if placeholder already exists
                try:
                    client.stat_object(bucket, placeholder_path)
                    logger.debug("Directory already exists: %s", dir_path)
                    continue
                except S3Error as e:
                    if e.code != "NoSuchKey":
                        raise

                # Create placeholder file
                data = BytesIO(b"")
                client.put_object(
                    bucket,
                    placeholder_path,
                    data,
                    length=0,
                    content_type="application/octet-stream",
                )
                logger.debug("Created directory: %s", dir_path)

            except S3Error as e:
                logger.error(
                    "Failed to create directory %s: %s",
                    dir_path,
                    e,
                )
                raise InitializationError(book_id, dir_path, str(e))

        logger.info(
            "AI data structure initialized for book %s",
            book_id,
        )
        return structure

    def verify_structure(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> dict[str, bool]:
        """
        Check if ai-data structure is properly initialized.

        Args:
            publisher_slug: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Dictionary mapping directory paths to existence status.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self.get_ai_data_paths(publisher_slug, book_id, book_name)
        result: dict[str, bool] = {}

        for dir_path in structure.get_all_directories():
            # Check if any objects exist with this prefix
            prefix = f"{dir_path}/"
            try:
                objects = list(client.list_objects(bucket, prefix=prefix, max_keys=1))
                result[dir_path] = len(objects) > 0
            except S3Error:
                result[dir_path] = False

        return result

    def structure_exists(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if ai-data structure exists (at least base path).

        Args:
            publisher_slug: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            True if base structure exists.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self.get_ai_data_paths(publisher_slug, book_id, book_name)
        prefix = f"{structure.base_path}/"

        try:
            objects = list(client.list_objects(bucket, prefix=prefix, max_keys=1))
            return len(objects) > 0
        except S3Error:
            return False


# Singleton instance
_structure_manager: AIDataStructureManager | None = None


def get_ai_data_structure_manager() -> AIDataStructureManager:
    """Get or create the global AI data structure manager instance."""
    global _structure_manager
    if _structure_manager is None:
        _structure_manager = AIDataStructureManager()
    return _structure_manager
