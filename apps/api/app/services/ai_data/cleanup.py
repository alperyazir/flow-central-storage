"""AI data cleanup manager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from minio.error import S3Error

from app.core.config import get_settings
from app.services.ai_data.models import (
    AIDataStructure,
    CleanupError,
    CleanupStats,
)
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class AIDataCleanupManager:
    """
    Manager for cleaning up AI data.

    Handles removal of ai-data content for re-processing.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize cleanup manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def _get_structure(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> AIDataStructure:
        """Get AI data structure paths."""
        return AIDataStructure.from_book_info(publisher_id, book_id, book_name)

    def cleanup_all(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> CleanupStats:
        """
        Remove all ai-data content for re-processing.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            CleanupStats with deletion counts.

        Raises:
            CleanupError: If cleanup fails critically.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self._get_structure(publisher_id, book_id, book_name)
        stats = CleanupStats()

        logger.info(
            "Cleaning up all ai-data for book %s at %s",
            book_id,
            structure.base_path,
        )

        # Delete all objects under ai-data/
        prefix = f"{structure.base_path}/"

        try:
            objects = client.list_objects(bucket, prefix=prefix, recursive=True)

            for obj in objects:
                try:
                    client.remove_object(bucket, obj.object_name)
                    stats.total_deleted += 1

                    # Track by subdirectory
                    if "/text/" in obj.object_name:
                        stats.text_deleted += 1
                    elif "/modules/" in obj.object_name:
                        stats.modules_deleted += 1
                    elif "/audio/" in obj.object_name:
                        stats.audio_deleted += 1
                    elif obj.object_name.endswith("vocabulary.json"):
                        stats.vocabulary_deleted += 1
                    elif obj.object_name.endswith("metadata.json"):
                        stats.metadata_deleted += 1

                    logger.debug("Deleted: %s", obj.object_name)

                except S3Error as e:
                    error_msg = f"Failed to delete {obj.object_name}: {e}"
                    stats.errors.append(error_msg)
                    logger.warning(error_msg)

        except S3Error as e:
            logger.error("Failed to list objects for cleanup: %s", e)
            raise CleanupError(book_id, structure.base_path, str(e))

        logger.info(
            "Cleanup complete for book %s: %d files deleted",
            book_id,
            stats.total_deleted,
        )
        return stats

    def cleanup_selective(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        directories: list[str],
    ) -> CleanupStats:
        """
        Remove specific subdirectories.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            directories: List of directory names to clean (text, modules, audio).

        Returns:
            CleanupStats with deletion counts.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self._get_structure(publisher_id, book_id, book_name)
        stats = CleanupStats()

        logger.info(
            "Selectively cleaning ai-data directories %s for book %s",
            directories,
            book_id,
        )

        # Map directory names to paths
        dir_paths = {
            "text": structure.text_path,
            "modules": structure.modules_path,
            "audio": structure.audio_path,
            "vocabulary": structure.vocabulary_path,
            "metadata": structure.metadata_path,
        }

        for dir_name in directories:
            path = dir_paths.get(dir_name)
            if path is None:
                stats.errors.append(f"Unknown directory: {dir_name}")
                continue

            # For files (vocabulary.json, metadata.json)
            if dir_name in ("vocabulary", "metadata"):
                try:
                    client.remove_object(bucket, path)
                    stats.total_deleted += 1
                    if dir_name == "vocabulary":
                        stats.vocabulary_deleted += 1
                    else:
                        stats.metadata_deleted += 1
                    logger.debug("Deleted file: %s", path)
                except S3Error as e:
                    if e.code != "NoSuchKey":
                        stats.errors.append(f"Failed to delete {path}: {e}")
                continue

            # For directories
            prefix = f"{path}/"
            try:
                objects = client.list_objects(bucket, prefix=prefix, recursive=True)

                for obj in objects:
                    try:
                        client.remove_object(bucket, obj.object_name)
                        stats.total_deleted += 1

                        if dir_name == "text":
                            stats.text_deleted += 1
                        elif dir_name == "modules":
                            stats.modules_deleted += 1
                        elif dir_name == "audio":
                            stats.audio_deleted += 1

                        logger.debug("Deleted: %s", obj.object_name)

                    except S3Error as e:
                        error_msg = f"Failed to delete {obj.object_name}: {e}"
                        stats.errors.append(error_msg)
                        logger.warning(error_msg)

            except S3Error as e:
                stats.errors.append(f"Failed to list {path}: {e}")

        logger.info(
            "Selective cleanup complete for book %s: %d files deleted",
            book_id,
            stats.total_deleted,
        )
        return stats

    def get_cleanup_stats(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> dict[str, int]:
        """
        Get count of files per directory without deleting.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Dictionary mapping directory names to file counts.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        structure = self._get_structure(publisher_id, book_id, book_name)
        counts: dict[str, int] = {
            "text": 0,
            "modules": 0,
            "audio": 0,
            "vocabulary": 0,
            "metadata": 0,
            "total": 0,
        }

        # Count objects by prefix
        dir_paths = {
            "text": structure.text_path,
            "modules": structure.modules_path,
            "audio": structure.audio_path,
        }

        for dir_name, path in dir_paths.items():
            prefix = f"{path}/"
            try:
                objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
                counts[dir_name] = len(objects)
                counts["total"] += len(objects)
            except S3Error:
                pass

        # Check single files
        try:
            client.stat_object(bucket, structure.vocabulary_path)
            counts["vocabulary"] = 1
            counts["total"] += 1
        except S3Error:
            pass

        try:
            client.stat_object(bucket, structure.metadata_path)
            counts["metadata"] = 1
            counts["total"] += 1
        except S3Error:
            pass

        return counts


# Singleton instance
_cleanup_manager: AIDataCleanupManager | None = None


def get_ai_data_cleanup_manager() -> AIDataCleanupManager:
    """Get or create the global AI data cleanup manager instance."""
    global _cleanup_manager
    if _cleanup_manager is None:
        _cleanup_manager = AIDataCleanupManager()
    return _cleanup_manager
