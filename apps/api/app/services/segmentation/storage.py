"""Storage service for segmented module data."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import TYPE_CHECKING

from minio.error import S3Error

from app.core.config import get_settings
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.segmentation.models import Module, SegmentationResult

logger = logging.getLogger(__name__)


class ModuleStorage:
    """
    Storage service for segmented module data.

    Handles saving and retrieving modules to/from MinIO
    following the ai-data structure:

    /publishers/{publisher_id}/books/{book_id}/{book_name}/
    └── ai-data/
        └── modules/
            ├── module_1.json
            ├── module_2.json
            └── segmentation_metadata.json
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize module storage.

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
        """Build path for segmentation metadata file."""
        return self._build_modules_path(publisher_slug, book_id, book_name, "segmentation_metadata.json")

    def save_module(self, result: SegmentationResult, module: Module) -> str:
        """
        Save a single module to MinIO.

        Args:
            result: Segmentation result (for path info).
            module: Module to save.

        Returns:
            Path to saved module file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_module_path(
            result.publisher_id,
            result.book_id,
            result.book_name,
            module.module_id,
        )

        # Convert module to JSON
        module_dict = module.to_dict()
        module_dict["extracted_at"] = result.segmented_at.isoformat()

        json_str = json.dumps(module_dict, indent=2, ensure_ascii=False)
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
            logger.debug("Saved module: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to save module %s: %s", path, e)
            raise

    def save_modules(self, result: SegmentationResult) -> list[str]:
        """
        Save all modules from segmentation result.

        Args:
            result: Segmentation result with modules.

        Returns:
            List of saved module paths.
        """
        saved_paths: list[str] = []

        logger.info(
            "Saving %d modules for book %s",
            len(result.modules),
            result.book_id,
        )

        for module in result.modules:
            path = self.save_module(result, module)
            saved_paths.append(path)

        logger.info("Saved %d module files", len(saved_paths))
        return saved_paths

    def save_segmentation_metadata(self, result: SegmentationResult) -> str:
        """
        Save segmentation metadata to MinIO.

        Args:
            result: Segmentation result.

        Returns:
            Path to saved metadata file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_metadata_path(
            result.publisher_id,
            result.book_id,
            result.book_name,
        )

        metadata = result.to_metadata_dict()
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
            logger.info("Saved segmentation metadata: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to save metadata %s: %s", path, e)
            raise

    def save_all(self, result: SegmentationResult) -> dict[str, list[str] | str]:
        """
        Save all segmentation data (modules + metadata).

        Args:
            result: Segmentation result.

        Returns:
            Dictionary with 'modules' and 'metadata' paths.
        """
        module_paths = self.save_modules(result)
        metadata_path = self.save_segmentation_metadata(result)

        return {
            "modules": module_paths,
            "metadata": metadata_path,
        }

    def get_module(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        module_id: int,
    ) -> dict | None:
        """
        Retrieve a single module.

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
    ) -> list[dict]:
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
        modules: list[dict] = []

        try:
            objects = client.list_objects(bucket, prefix=prefix, recursive=False)

            for obj in objects:
                # Skip metadata file
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

    def get_segmentation_metadata(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> dict | None:
        """
        Retrieve segmentation metadata.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Metadata dictionary or None if not found.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_metadata_path(publisher_slug, book_id, book_name)

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

    def modules_exist(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if segmentation data exists for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            True if segmentation metadata exists.
        """
        return self.get_segmentation_metadata(publisher_slug, book_id, book_name) is not None

    def cleanup_modules_directory(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> int:
        """
        Delete existing module files before re-segmentation.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Number of objects deleted.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        prefix = self._build_modules_path(publisher_slug, book_id, book_name) + "/"
        logger.info("Cleaning up modules directory: %s", prefix)

        objects = client.list_objects(bucket, prefix=prefix, recursive=True)
        deleted_count = 0

        for obj in objects:
            try:
                client.remove_object(bucket, obj.object_name)
                deleted_count += 1
                logger.debug("Deleted: %s", obj.object_name)
            except S3Error as e:
                logger.warning("Failed to delete %s: %s", obj.object_name, e)

        logger.info("Deleted %d objects from modules directory", deleted_count)
        return deleted_count


# Singleton instance
_module_storage: ModuleStorage | None = None


def get_module_storage() -> ModuleStorage:
    """Get or create the global module storage instance."""
    global _module_storage
    if _module_storage is None:
        _module_storage = ModuleStorage()
    return _module_storage
