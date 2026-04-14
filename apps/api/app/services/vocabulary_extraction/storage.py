"""Storage service for vocabulary extraction results."""

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
    from app.services.vocabulary_extraction.models import (
        BookVocabularyResult,
        ModuleVocabularyResult,
    )

logger = logging.getLogger(__name__)


class VocabularyStorage:
    """
    Storage service for vocabulary extraction results.

    Saves vocabulary.json and updates module JSONs with vocabulary_ids.

    Storage path:
    /publishers/{publisher_id}/books/{book_id}/{book_name}/
    └── ai-data/
        ├── modules/
        │   ├── module_1.json  <- Updated with vocabulary_ids
        │   └── module_2.json
        └── vocabulary.json    <- NEW: Master vocabulary list
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize vocabulary storage.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def _build_ai_data_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        *path_parts: str,
    ) -> str:
        """Build MinIO path within ai-data directory."""
        # Path: {publisher_id}/books/{book_name}/ai-data (book_id not in path)
        base = f"{publisher_slug}/books/{book_name}/ai-data"
        if path_parts:
            return f"{base}/{'/'.join(path_parts)}"
        return base

    def _build_vocabulary_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build path for vocabulary.json file."""
        return self._build_ai_data_path(publisher_slug, book_id, book_name, "vocabulary.json")

    def _build_module_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_id: int,
    ) -> str:
        """Build path for a module JSON file."""
        filename = f"module_{module_id}.json"
        return self._build_ai_data_path(publisher_slug, book_id, book_name, "modules", filename)

    def _build_metadata_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build path for vocabulary extraction metadata file."""
        return self._build_ai_data_path(publisher_slug, book_id, book_name, "vocabulary_metadata.json")

    def load_vocabulary(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> dict[str, Any] | None:
        """
        Load existing vocabulary.json if any.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Vocabulary dictionary or None if not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_vocabulary_path(publisher_slug, book_id, book_name)

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

    def save_vocabulary(
        self,
        book_result: BookVocabularyResult,
    ) -> str:
        """
        Save vocabulary.json to MinIO.

        Args:
            book_result: Complete book vocabulary result.

        Returns:
            Path to saved vocabulary file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_vocabulary_path(
            book_result.publisher_id,
            book_result.book_id,
            book_result.book_name,
        )

        # Use the vocabulary.json format
        vocabulary_data = book_result.to_dict()

        json_str = json.dumps(vocabulary_data, indent=2, ensure_ascii=False)
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
            logger.info(
                "Saved vocabulary.json with %d words: %s",
                book_result.total_words,
                path,
            )
            return path
        except S3Error as e:
            logger.error("Failed to save vocabulary %s: %s", path, e)
            raise

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

        prefix = self._build_ai_data_path(publisher_slug, book_id, book_name, "modules") + "/"
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

    def update_module_vocabulary_ids(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_result: ModuleVocabularyResult,
    ) -> str | None:
        """
        Update an existing module JSON with vocabulary_ids.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            module_result: Vocabulary result for the module.

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
                "Module %d not found for vocabulary update: %s",
                module_result.module_id,
                path,
            )
            return None

        # Update with vocabulary_ids
        existing["vocabulary_ids"] = module_result.vocabulary_ids
        existing["vocabulary_extracted_at"] = module_result.extracted_at.isoformat()

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
            logger.debug(
                "Updated module %d with %d vocabulary_ids: %s",
                module_result.module_id,
                len(module_result.vocabulary_ids),
                path,
            )
            return path
        except S3Error as e:
            logger.error("Failed to update module %s: %s", path, e)
            raise

    def update_all_modules(
        self,
        book_result: BookVocabularyResult,
    ) -> dict[str, list[str] | int]:
        """
        Update all modules for a book with vocabulary_ids.

        Args:
            book_result: Complete book vocabulary result.

        Returns:
            Dictionary with 'updated' paths and 'failed' count.
        """
        updated_paths: list[str] = []
        failed_count = 0

        logger.info(
            "Updating %d modules for book %s with vocabulary_ids",
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
                path = self.update_module_vocabulary_ids(
                    publisher_slug=book_result.publisher_id,
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
                    "Failed to update module %d with vocabulary: %s",
                    module_result.module_id,
                    e,
                )
                failed_count += 1

        logger.info(
            "Updated %d modules with vocabulary_ids, %d failed",
            len(updated_paths),
            failed_count,
        )

        return {
            "updated": updated_paths,
            "failed": failed_count,
        }

    def save_extraction_metadata(
        self,
        book_result: BookVocabularyResult,
    ) -> str:
        """
        Save vocabulary extraction metadata to MinIO.

        Args:
            book_result: Complete book vocabulary result.

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

        metadata = book_result.to_metadata_dict()

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
            logger.info("Saved vocabulary extraction metadata: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to save metadata %s: %s", path, e)
            raise

    def save_all(
        self,
        book_result: BookVocabularyResult,
    ) -> dict[str, Any]:
        """
        Save vocabulary.json, update all modules, and save metadata.

        Args:
            book_result: Complete book vocabulary result.

        Returns:
            Dictionary with 'vocabulary', 'updated', 'failed', and 'metadata' paths.
        """
        # Save master vocabulary.json
        vocabulary_path = self.save_vocabulary(book_result)

        # Update module JSONs with vocabulary_ids
        update_result = self.update_all_modules(book_result)

        # Save extraction metadata
        metadata_path = self.save_extraction_metadata(book_result)

        return {
            "vocabulary": vocabulary_path,
            "updated": update_result["updated"],
            "failed": update_result["failed"],
            "metadata": metadata_path,
        }


# Singleton instance
_vocabulary_storage: VocabularyStorage | None = None


def get_vocabulary_storage() -> VocabularyStorage:
    """Get or create the global vocabulary storage instance."""
    global _vocabulary_storage
    if _vocabulary_storage is None:
        _vocabulary_storage = VocabularyStorage()
    return _vocabulary_storage
