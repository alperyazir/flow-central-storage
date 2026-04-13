"""Storage service for updating module JSON files with topic analysis results."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any

from minio.error import S3Error

from app.core.config import get_settings
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.topic_analysis.models import BookAnalysisResult, ModuleAnalysisResult

logger = logging.getLogger(__name__)


class TopicStorage:
    """
    Storage service for updating module JSON files with topic analysis results.

    Updates existing module JSON files created by segmentation (Story 10.5)
    with topic analysis data from this story.

    Storage path:
    /publishers/{publisher_id}/books/{book_id}/{book_name}/
    └── ai-data/
        └── modules/
            ├── module_1.json  <- Updated with topics, language, difficulty
            ├── module_2.json
            └── topic_analysis_metadata.json  <- NEW: Analysis metadata
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize topic storage.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def _build_modules_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        *path_parts: str,
    ) -> str:
        """Build MinIO path within ai-data/modules directory."""
        # Path: {publisher_id}/books/{book_name}/ai-data/modules (book_id not in path)
        base = f"{publisher_slug}/books/{book_name}/ai-data/modules"
        if path_parts:
            return f"{base}/{'/'.join(path_parts)}"
        return base

    def _build_module_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_id: int,
    ) -> str:
        """Build path for a module JSON file."""
        filename = f"module_{module_id}.json"
        return self._build_modules_path(publisher_slug, book_id, book_name, filename)

    def _build_metadata_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build path for topic analysis metadata file."""
        return self._build_modules_path(publisher_slug, book_id, book_name, "topic_analysis_metadata.json")

    def get_module(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_id: int,
    ) -> dict[str, Any] | None:
        """
        Retrieve a single module JSON.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            module_id: Module ID.

        Returns:
            Module dictionary or None if not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_module_path(publisher_slug, book_id, book_name, module_id)

        try:
            response = client.get_object(bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            return json.loads(data.decode("utf-8"))
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise

    def list_modules(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> list[dict[str, Any]]:
        """
        List all modules for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            List of module dictionaries.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        prefix = self._build_modules_path(publisher_slug, book_id, book_name) + "/"
        modules: list[dict[str, Any]] = []

        try:
            objects = client.list_objects(bucket, prefix=prefix, recursive=False)

            for obj in objects:
                # Skip metadata files
                if "metadata" in obj.object_name:
                    continue
                if not obj.object_name.endswith(".json"):
                    continue

                # Load module
                response = client.get_object(bucket, obj.object_name)
                data = response.read()
                response.close()
                response.release_conn()
                modules.append(json.loads(data.decode("utf-8")))

        except S3Error as e:
            logger.error("Failed to list modules: %s", e)
            raise

        # Sort by module_id
        modules.sort(key=lambda m: m.get("module_id", 0))
        return modules

    def update_module_with_topics(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_result: ModuleAnalysisResult,
    ) -> str | None:
        """
        Update an existing module JSON with topic analysis results.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            module_result: Analysis result for the module.

        Returns:
            Path to updated module file, or None if module not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_module_path(publisher_slug, book_id, book_name, module_result.module_id)

        # Load existing module
        existing = self.get_module(publisher_slug, book_id, book_name, module_result.module_id)
        if existing is None:
            logger.warning(
                "Module %d not found for update: %s",
                module_result.module_id,
                path,
            )
            return None

        # Update with topic analysis results
        topic_result = module_result.topic_result
        existing["topics"] = topic_result.topics
        existing["language"] = topic_result.language
        existing["difficulty"] = topic_result.difficulty
        existing["grammar_points"] = topic_result.grammar_points
        existing["target_skills"] = topic_result.target_skills
        existing["topic_analysis_at"] = module_result.analyzed_at.isoformat()

        # Save updated module
        json_str = json.dumps(existing, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode("utf-8")
        data = BytesIO(json_bytes)

        try:
            client.put_object(
                bucket,
                path,
                data,
                length=len(json_bytes),
                content_type="application/json; charset=utf-8",
            )
            logger.debug("Updated module with topics: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to update module %s: %s", path, e)
            raise

    def update_all_modules(
        self,
        book_result: BookAnalysisResult,
    ) -> dict[str, list[str] | int]:
        """
        Update all modules for a book with topic analysis results.

        Args:
            book_result: Complete book analysis result.

        Returns:
            Dictionary with 'updated' paths and 'failed' count.
        """
        updated_paths: list[str] = []
        failed_count = 0

        logger.info(
            "Updating %d modules for book %s with topic analysis",
            len(book_result.module_results),
            book_result.book_id,
        )

        for module_result in book_result.module_results:
            if not module_result.success:
                logger.debug(
                    "Skipping failed module %d",
                    module_result.module_id,
                )
                failed_count += 1
                continue

            try:
                path = self.update_module_with_topics(
                    publisher_id=book_result.publisher_id,
                    book_id=book_result.book_id,
                    book_name=book_result.book_name,
                    module_result=module_result,
                )
                if path:
                    updated_paths.append(path)
                else:
                    failed_count += 1
            except Exception as e:
                logger.error(
                    "Failed to update module %d: %s",
                    module_result.module_id,
                    e,
                )
                failed_count += 1

        logger.info(
            "Updated %d modules, %d failed",
            len(updated_paths),
            failed_count,
        )

        return {
            "updated": updated_paths,
            "failed": failed_count,
        }

    def save_analysis_metadata(
        self,
        book_result: BookAnalysisResult,
    ) -> str:
        """
        Save topic analysis metadata to MinIO.

        Args:
            book_result: Complete book analysis result.

        Returns:
            Path to saved metadata file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_metadata_path(
            book_result.publisher_id,
            book_result.book_id,
            book_result.book_name,
        )

        metadata = book_result.to_dict()
        # Remove full module data from metadata (it's in individual files)
        del metadata["modules"]

        json_str = json.dumps(metadata, indent=2, ensure_ascii=False)
        json_bytes = json_str.encode("utf-8")
        data = BytesIO(json_bytes)

        try:
            client.put_object(
                bucket,
                path,
                data,
                length=len(json_bytes),
                content_type="application/json; charset=utf-8",
            )
            logger.info("Saved topic analysis metadata: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to save metadata %s: %s", path, e)
            raise

    def save_all(
        self,
        book_result: BookAnalysisResult,
    ) -> dict[str, Any]:
        """
        Update all modules and save metadata.

        Args:
            book_result: Complete book analysis result.

        Returns:
            Dictionary with 'updated', 'failed', and 'metadata' paths.
        """
        update_result = self.update_all_modules(book_result)
        metadata_path = self.save_analysis_metadata(book_result)

        return {
            "updated": update_result["updated"],
            "failed": update_result["failed"],
            "metadata": metadata_path,
        }


# Singleton instance
_topic_storage: TopicStorage | None = None


def get_topic_storage() -> TopicStorage:
    """Get or create the global topic storage instance."""
    global _topic_storage
    if _topic_storage is None:
        _topic_storage = TopicStorage()
    return _topic_storage
