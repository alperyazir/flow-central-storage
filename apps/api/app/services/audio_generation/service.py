"""Audio generation service for vocabulary pronunciations."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from app.core.config import get_settings
from app.services.audio_generation.models import (
    AudioFile,
    BookAudioResult,
    WordAudioResult,
)
from app.services.tts import TTSBatchItem, TTSProviderError, get_tts_service

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.tts import TTSService

logger = logging.getLogger(__name__)


class AudioGenerationService:
    """
    Service for generating audio pronunciations for vocabulary words.

    Uses TTSService for text-to-speech synthesis with automatic
    fallback between providers.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        tts_service: TTSService | None = None,
    ) -> None:
        """
        Initialize Audio Generation Service.

        Args:
            settings: Application settings. If not provided, will load from environment.
            tts_service: Override TTS service instance for testing.
        """
        self.settings = settings or get_settings()
        self._tts_service = tts_service

    @property
    def tts_service(self) -> TTSService:
        """Get the TTS service instance."""
        if self._tts_service is None:
            self._tts_service = get_tts_service()
        return self._tts_service

    async def generate_word_audio(
        self,
        word: str,
        word_id: str,
        language: str,
        book_id: str,
    ) -> WordAudioResult:
        """
        Generate audio for a single word.

        Args:
            word: The word text to synthesize.
            word_id: Unique identifier for the word (used for file naming).
            language: Language code (e.g., "en", "tr").
            book_id: Book identifier for error context.

        Returns:
            WordAudioResult with success status and audio data.
        """
        try:
            response = await self.tts_service.synthesize_text(
                text=word,
                language=language,
                use_fallback=True,
            )

            # Create audio file metadata
            file_path = f"audio/vocabulary/{language}/{word_id}.mp3"
            audio_file = AudioFile(
                word_id=word_id,
                word=word,
                language=language,
                file_path=file_path,
                duration_ms=response.duration_ms,
            )

            return WordAudioResult(
                word_id=word_id,
                word=word,
                language=language,
                success=True,
                audio_file=audio_file,
            )

        except TTSProviderError as e:
            logger.warning(f"[AudioGeneration] TTS failed for word '{word}' ({language}): {e}")
            return WordAudioResult(
                word_id=word_id,
                word=word,
                language=language,
                success=False,
                error_message=str(e),
            )
        except Exception as e:
            logger.error(f"[AudioGeneration] Unexpected error for word '{word}' ({language}): {e}")
            return WordAudioResult(
                word_id=word_id,
                word=word,
                language=language,
                success=False,
                error_message=f"Unexpected error: {e}",
            )

    async def generate_vocabulary_audio(
        self,
        vocabulary: list[dict[str, Any]],
        book_id: str,
        publisher_slug: str,
        book_name: str,
        language: str = "en",
        translation_language: str = "tr",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> tuple[BookAudioResult, dict[str, bytes]]:
        """
        Generate audio for all vocabulary words.

        Args:
            vocabulary: List of vocabulary word dictionaries.
            book_id: Book identifier.
            publisher_slug: Publisher slug for storage path.
            book_name: Book name for context.
            language: Primary language code.
            translation_language: Translation language code.
            progress_callback: Optional callback(current, total) for progress updates.

        Returns:
            Tuple of (BookAudioResult, dict mapping file_path to audio bytes).
        """
        if not vocabulary:
            logger.info(f"[AudioGeneration] No vocabulary words for book {book_id}")
            return BookAudioResult(
                book_id=book_id,
                publisher_id=publisher_slug,
                book_name=book_name,
                language=language,
                translation_language=translation_language,
                total_words=0,
            ), {}

        # Build batch items for all words (primary + translations)
        batch_items: list[TTSBatchItem] = []
        item_metadata: list[dict[str, str]] = []  # Track which word/language each item is

        for word_data in vocabulary:
            word_id = word_data.get("id", "")
            word = word_data.get("word", "")
            translation = word_data.get("translation", "")

            if word:
                batch_items.append(
                    TTSBatchItem(
                        text=word,
                        language=language,
                        id=f"{word_id}_word",
                    )
                )
                item_metadata.append(
                    {
                        "word_id": word_id,
                        "word": word,
                        "language": language,
                        "type": "word",
                    }
                )

            if translation:
                # Use word_id for translation file too (different folder)
                translation_id = self._slugify(translation) if translation else word_id
                batch_items.append(
                    TTSBatchItem(
                        text=translation,
                        language=translation_language,
                        id=f"{word_id}_translation",
                    )
                )
                item_metadata.append(
                    {
                        "word_id": translation_id,
                        "word": translation,
                        "language": translation_language,
                        "type": "translation",
                        "original_word_id": word_id,
                    }
                )

        total_items = len(batch_items)
        logger.info(
            f"[AudioGeneration] Generating audio for {len(vocabulary)} words "
            f"({total_items} total items including translations)"
        )

        # Process in batches using TTS batch processing
        concurrency = getattr(self.settings, "audio_generation_concurrency", 5)
        batch_result = await self.tts_service.synthesize_batch(
            items=batch_items,
            concurrency=concurrency,
            use_fallback=True,
        )

        # Process results
        word_results: list[WordAudioResult] = []
        audio_files: list[AudioFile] = []
        audio_data: dict[str, bytes] = {}

        for i, (response, meta) in enumerate(zip(batch_result.results, item_metadata)):
            if progress_callback:
                progress_callback(i + 1, total_items)

            word_id = meta["word_id"]
            word = meta["word"]
            lang = meta["language"]
            file_path = f"audio/vocabulary/{lang}/{word_id}.mp3"

            if response is not None:
                audio_file = AudioFile(
                    word_id=word_id,
                    word=word,
                    language=lang,
                    file_path=file_path,
                    duration_ms=response.duration_ms,
                )
                word_results.append(
                    WordAudioResult(
                        word_id=word_id,
                        word=word,
                        language=lang,
                        success=True,
                        audio_file=audio_file,
                    )
                )
                audio_files.append(audio_file)
                audio_data[file_path] = response.audio_data
            else:
                # Find error message for this item
                error_msg = "TTS synthesis failed"
                for idx, err in batch_result.errors:
                    if idx == i:
                        error_msg = err
                        break

                word_results.append(
                    WordAudioResult(
                        word_id=word_id,
                        word=word,
                        language=lang,
                        success=False,
                        error_message=error_msg,
                    )
                )

        result = BookAudioResult(
            book_id=book_id,
            publisher_id=publisher_slug,
            book_name=book_name,
            language=language,
            translation_language=translation_language,
            total_words=len(vocabulary),
            word_results=word_results,
            audio_files=audio_files,
        )

        logger.info(f"[AudioGeneration] Completed: {result.generated_count} generated, {result.failed_count} failed")

        return result, audio_data

    def _slugify(self, text: str) -> str:
        """Create a URL-safe slug from text."""
        import re

        slug = text.lower()
        slug = re.sub(r"[^a-z0-9]+", "_", slug)
        slug = slug.strip("_")
        return slug


# Singleton instance for convenience
_audio_generation_service: AudioGenerationService | None = None


def get_audio_generation_service() -> AudioGenerationService:
    """Get or create the global Audio Generation service instance."""
    global _audio_generation_service
    if _audio_generation_service is None:
        _audio_generation_service = AudioGenerationService()
    return _audio_generation_service
