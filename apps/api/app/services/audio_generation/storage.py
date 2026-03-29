"""Storage service for audio generation results."""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any

from minio.error import S3Error

from app.core.config import get_settings
from app.services.audio_generation.models import (
    AudioFile,
    NoVocabularyFoundError,
    StorageError,
)
from app.services.minio import get_minio_client

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class AudioStorage:
    """
    Storage service for audio generation results.

    Saves audio MP3 files and updates vocabulary.json with audio paths.

    Storage path:
    /publishers/{publisher_id}/books/{book_id}/{book_name}/
    └── ai-data/
        ├── vocabulary.json           <- Updated with audio paths
        └── audio/
            └── vocabulary/
                ├── en/               <- English pronunciations
                │   ├── word1.mp3
                │   └── word2.mp3
                └── tr/               <- Turkish pronunciations
                    ├── word1.mp3
                    └── word2.mp3
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize audio storage.

        Args:
            settings: Application settings.
        """
        self.settings = settings or get_settings()

    def _build_ai_data_path(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        *path_parts: str,
    ) -> str:
        """Build MinIO path within ai-data directory."""
        # Path: {publisher_id}/books/{book_name}/ai-data (book_id not in path)
        base = f"{publisher_id}/books/{book_name}/ai-data"
        if path_parts:
            return f"{base}/{'/'.join(path_parts)}"
        return base

    def _build_vocabulary_path(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build path for vocabulary.json file."""
        return self._build_ai_data_path(publisher_id, book_id, book_name, "vocabulary.json")

    def _build_audio_path(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        language: str,
        word_id: str,
    ) -> str:
        """Build path for an audio file."""
        return self._build_ai_data_path(
            publisher_id, book_id, book_name, "audio", "vocabulary", language, f"{word_id}.mp3"
        )

    def _build_audio_prefix(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> str:
        """Build prefix for audio vocabulary directory."""
        return self._build_ai_data_path(publisher_id, book_id, book_name, "audio", "vocabulary") + "/"

    def load_vocabulary(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> dict[str, Any]:
        """
        Load vocabulary.json from storage.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Vocabulary dictionary.

        Raises:
            NoVocabularyFoundError: If vocabulary.json doesn't exist.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_vocabulary_path(publisher_id, book_id, book_name)

        try:
            response = client.get_object(bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            return json.loads(data.decode("utf-8"))
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise NoVocabularyFoundError(book_id, path)
            raise

    def save_audio_file(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        audio_file: AudioFile,
        audio_data: bytes,
    ) -> str:
        """
        Save a single audio MP3 file to MinIO.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            audio_file: Audio file metadata.
            audio_data: Raw audio bytes.

        Returns:
            Full path to saved audio file.

        Raises:
            StorageError: If save fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        path = self._build_audio_path(publisher_id, book_id, book_name, audio_file.language, audio_file.word_id)

        data = BytesIO(audio_data)

        try:
            client.put_object(
                bucket,
                path,
                data,
                length=len(audio_data),
                content_type="audio/mpeg",
            )
            logger.debug(
                "Saved audio file: %s (%d bytes)",
                path,
                len(audio_data),
            )
            return path
        except S3Error as e:
            logger.error("Failed to save audio file %s: %s", path, e)
            raise StorageError(
                book_id=book_id,
                operation="save",
                path=path,
                reason=str(e),
            )

    def save_all_audio(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        audio_files: list[AudioFile],
        audio_data: dict[str, bytes],
    ) -> dict[str, Any]:
        """
        Save all generated audio files to MinIO.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            audio_files: List of audio file metadata.
            audio_data: Dictionary mapping file_path to audio bytes.

        Returns:
            Dictionary with 'saved' count and 'failed' count.
        """
        saved_count = 0
        failed_count = 0
        saved_paths: list[str] = []

        logger.info(
            "Saving %d audio files for book %s",
            len(audio_files),
            book_id,
        )

        for audio_file in audio_files:
            # Get audio data using the relative file_path
            data = audio_data.get(audio_file.file_path)
            if data is None:
                logger.warning(
                    "No audio data for %s, skipping",
                    audio_file.file_path,
                )
                failed_count += 1
                continue

            try:
                path = self.save_audio_file(
                    publisher_id=publisher_id,
                    book_id=book_id,
                    book_name=book_name,
                    audio_file=audio_file,
                    audio_data=data,
                )
                saved_paths.append(path)
                saved_count += 1
            except StorageError as e:
                logger.error("Failed to save audio: %s", e)
                failed_count += 1

        logger.info(
            "Saved %d audio files, %d failed",
            saved_count,
            failed_count,
        )

        return {
            "saved": saved_count,
            "failed": failed_count,
            "paths": saved_paths,
        }

    def update_vocabulary_audio_paths(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
        audio_files: list[AudioFile],
    ) -> str:
        """
        Update vocabulary.json with audio paths for each word.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.
            audio_files: List of generated audio files.

        Returns:
            Path to updated vocabulary.json.

        Raises:
            NoVocabularyFoundError: If vocabulary.json doesn't exist.
            StorageError: If save fails.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Load existing vocabulary
        vocabulary = self.load_vocabulary(publisher_id, book_id, book_name)

        # Build audio path lookup by word_id and language
        # audio_files contains both word and translation audio
        audio_lookup: dict[str, dict[str, str]] = {}
        for af in audio_files:
            # Use the relative path from ai-data/ for the vocabulary.json
            audio_lookup.setdefault(af.word_id, {})[af.language] = af.file_path

        # Update each word with audio paths
        primary_lang = vocabulary.get("language", "en")
        translation_lang = vocabulary.get("translation_language", "tr")
        updated_count = 0

        for word in vocabulary.get("words", []):
            word_id = word.get("id", "")
            if not word_id:
                continue

            audio_paths = {}

            # Check for primary word audio
            if word_id in audio_lookup and primary_lang in audio_lookup[word_id]:
                audio_paths["word"] = audio_lookup[word_id][primary_lang]

            # Check for translation audio
            # Translation audio is stored under the slugified translation text
            translation = word.get("translation", "")
            if translation:
                translation_id = self._slugify(translation)
                if translation_id in audio_lookup and translation_lang in audio_lookup[translation_id]:
                    audio_paths["translation"] = audio_lookup[translation_id][translation_lang]

            if audio_paths:
                word["audio"] = audio_paths
                updated_count += 1

        # Save updated vocabulary
        path = self._build_vocabulary_path(publisher_id, book_id, book_name)

        json_str = json.dumps(vocabulary, indent=2, ensure_ascii=False)
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
                "Updated vocabulary.json with audio paths for %d words: %s",
                updated_count,
                path,
            )
            return path
        except S3Error as e:
            logger.error("Failed to update vocabulary %s: %s", path, e)
            raise StorageError(
                book_id=book_id,
                operation="update",
                path=path,
                reason=str(e),
            )

    def cleanup_audio_directory(
        self,
        publisher_id: int,
        book_id: str,
        book_name: str,
    ) -> int:
        """
        Clean up existing audio files before re-generation.

        Args:
            publisher_id: Publisher ID (integer).
            book_id: Book identifier.
            book_name: Book folder name.

        Returns:
            Number of files deleted.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        prefix = self._build_audio_prefix(publisher_id, book_id, book_name)
        deleted_count = 0

        try:
            objects = client.list_objects(bucket, prefix=prefix, recursive=True)
            objects_to_delete = [obj.object_name for obj in objects]

            for obj_name in objects_to_delete:
                try:
                    client.remove_object(bucket, obj_name)
                    deleted_count += 1
                except S3Error as e:
                    logger.warning("Failed to delete %s: %s", obj_name, e)

            if deleted_count > 0:
                logger.info(
                    "Cleaned up %d existing audio files from %s",
                    deleted_count,
                    prefix,
                )

        except S3Error as e:
            logger.error("Failed to list audio files for cleanup: %s", e)

        return deleted_count

    def _slugify(self, text: str) -> str:
        """Create a URL-safe slug from text."""
        import re

        slug = text.lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        return slug


# Singleton instance
_audio_storage: AudioStorage | None = None


def get_audio_storage() -> AudioStorage:
    """Get or create the global audio storage instance."""
    global _audio_storage
    if _audio_storage is None:
        _audio_storage = AudioStorage()
    return _audio_storage
