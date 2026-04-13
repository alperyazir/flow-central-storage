"""Storage service for AI-extracted text data."""

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
    from app.services.pdf.models import PDFExtractionResult

logger = logging.getLogger(__name__)


class AIDataStorage:
    """
    Storage service for AI-generated book data.

    Handles saving extracted text and metadata to MinIO
    following the ai-data structure:

    /publishers/{publisher_id}/books/{book_id}/{book_name}/
    └── ai-data/
        └── text/
            ├── page_001.txt
            ├── page_002.txt
            └── extraction_metadata.json
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize the AI data storage service.

        Args:
            settings: Application settings. If not provided, will load from environment.
        """
        self.settings = settings or get_settings()

    def _build_ai_data_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        *path_parts: str,
    ) -> str:
        """
        Build MinIO path within ai-data directory.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier (not used in path).
            book_name: Book name (folder name).
            *path_parts: Additional path segments.

        Returns:
            Complete MinIO object path.
        """
        # Path: {publisher_id}/books/{book_name}/ai-data (book_id not in path)
        base = f"{publisher_slug}/books/{book_name}/ai-data"
        if path_parts:
            return f"{base}/{'/'.join(path_parts)}"
        return base

    def _build_text_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        page_number: int,
    ) -> str:
        """
        Build path for a page text file.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book name.
            page_number: Page number (1-indexed).

        Returns:
            MinIO object path for the page text file.
        """
        filename = f"page_{page_number:03d}.txt"
        return self._build_ai_data_path(publisher_slug, book_id, book_name, "text", filename)

    def _build_metadata_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> str:
        """
        Build path for extraction metadata file.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book name.

        Returns:
            MinIO object path for the metadata file.
        """
        return self._build_ai_data_path(publisher_slug, book_id, book_name, "text", "extraction_metadata.json")

    def save_extracted_text(self, result: PDFExtractionResult) -> list[str]:
        """
        Save extracted text pages to MinIO.

        Args:
            result: PDF extraction result containing all page texts.

        Returns:
            List of saved object paths.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket
        saved_paths: list[str] = []

        logger.info(
            "Saving %d text files for book %s",
            len(result.pages),
            result.book_id,
        )

        for page in result.pages:
            path = self._build_text_path(
                result.publisher_id,
                result.book_id,
                result.book_name,
                page.page_number,
            )

            # Convert text to bytes
            text_bytes = page.text.encode("utf-8")
            data = BytesIO(text_bytes)

            try:
                client.put_object(
                    bucket,
                    path,
                    data,
                    length=len(text_bytes),
                    content_type="text/plain; charset=utf-8",
                )
                saved_paths.append(path)
                logger.debug("Saved: %s", path)
            except S3Error as e:
                logger.error("Failed to save %s: %s", path, e)
                raise

        logger.info("Saved %d text files", len(saved_paths))
        return saved_paths

    def save_extraction_metadata(self, result: PDFExtractionResult) -> str:
        """
        Save extraction metadata to MinIO.

        Args:
            result: PDF extraction result.

        Returns:
            Path to the saved metadata file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_metadata_path(
            result.publisher_id,
            result.book_id,
            result.book_name,
        )

        # Create metadata dictionary
        metadata = result.to_metadata_dict()

        # Add per-page method info
        metadata["pages"] = [
            {
                "page_number": p.page_number,
                "method": p.method.value,
                "word_count": p.word_count,
                "char_count": p.char_count,
            }
            for p in result.pages
        ]

        # Convert to JSON bytes
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
            logger.info("Saved extraction metadata: %s", path)
            return path
        except S3Error as e:
            logger.error("Failed to save metadata %s: %s", path, e)
            raise

    def save_full_text(self, result: PDFExtractionResult) -> str:
        """
        Save combined full text of all pages to a single file.

        Args:
            result: PDF extraction result containing all page texts.

        Returns:
            Path to the saved full text file.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_ai_data_path(
            result.publisher_id,
            result.book_id,
            result.book_name,
            "text",
            "full_text.txt",
        )

        # Combine all pages with page markers
        parts = []
        for page in result.pages:
            parts.append(f"\n{'=' * 60}")
            parts.append(f"PAGE {page.page_number}")
            parts.append(f"{'=' * 60}\n")
            parts.append(page.text)
            parts.append("\n")

        full_text = "\n".join(parts)
        text_bytes = full_text.encode("utf-8")
        data = BytesIO(text_bytes)

        try:
            client.put_object(
                bucket,
                path,
                data,
                length=len(text_bytes),
                content_type="text/plain; charset=utf-8",
            )
            logger.info("Saved full text: %s (%d bytes)", path, len(text_bytes))
            return path
        except S3Error as e:
            logger.error("Failed to save full text %s: %s", path, e)
            raise

    def save_all(self, result: PDFExtractionResult) -> dict[str, list[str] | str]:
        """
        Save all extracted data (text files + full text + metadata).

        Args:
            result: PDF extraction result.

        Returns:
            Dictionary with 'text_files', 'full_text', and 'metadata' paths.
        """
        text_paths = self.save_extracted_text(result)
        full_text_path = self.save_full_text(result)
        metadata_path = self.save_extraction_metadata(result)

        return {
            "text_files": text_paths,
            "full_text": full_text_path,
            "metadata": metadata_path,
        }

    def cleanup_text_directory(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> int:
        """
        Delete existing text files before re-extraction.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book name.

        Returns:
            Number of objects deleted.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Build prefix for text directory
        prefix = self._build_ai_data_path(publisher_slug, book_id, book_name, "text/")

        logger.info("Cleaning up text directory: %s", prefix)

        # List and delete all objects with this prefix
        objects = client.list_objects(bucket, prefix=prefix, recursive=True)
        deleted_count = 0

        for obj in objects:
            try:
                client.remove_object(bucket, obj.object_name)
                deleted_count += 1
                logger.debug("Deleted: %s", obj.object_name)
            except S3Error as e:
                logger.warning("Failed to delete %s: %s", obj.object_name, e)

        logger.info("Deleted %d objects from text directory", deleted_count)
        return deleted_count

    def text_exists(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> bool:
        """
        Check if extracted text already exists for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book name.

        Returns:
            True if extraction metadata exists.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_metadata_path(publisher_slug, book_id, book_name)

        try:
            client.stat_object(bucket, path)
            return True
        except S3Error as e:
            if e.code == "NoSuchKey":
                return False
            raise

    def get_extraction_metadata(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> dict | None:
        """
        Retrieve extraction metadata for a book.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book name.

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


# Singleton instance
_ai_storage: AIDataStorage | None = None


def get_ai_storage() -> AIDataStorage:
    """Get or create the global AI data storage instance."""
    global _ai_storage
    if _ai_storage is None:
        _ai_storage = AIDataStorage()
    return _ai_storage
