"""Vocabulary extraction service for extracting vocabulary from module content."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from cefrpy import CEFRAnalyzer

from app.core.config import get_settings
from app.services.llm import LLMProviderError, get_llm_service
from app.services.vocabulary_extraction.models import (
    BookVocabularyResult,
    InvalidLLMResponseError,
    ModuleVocabularyResult,
    NoModulesFoundError,
    VocabularyWord,
)
from app.services.vocabulary_extraction.prompts import (
    SYSTEM_PROMPT,
    build_simple_vocabulary_prompt,
    build_vocabulary_extraction_prompt,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.llm.service import LLMService

logger = logging.getLogger(__name__)


class VocabularyExtractionService:
    """
    Service for extracting vocabulary from module content.

    Features:
    - Extract vocabulary words with definitions, translations, and metadata
    - Use LLM for intelligent vocabulary extraction
    - Handle LLM failures gracefully with fallback strategies
    - Deduplicate vocabulary across modules
    - Support for multi-module book extraction
    """

    def __init__(
        self,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        """
        Initialize vocabulary extraction service.

        Args:
            settings: Application settings.
            llm_service: LLM service for AI extraction.
        """
        self.settings = settings or get_settings()
        self._llm_service = llm_service
        self._cefr_analyzer = CEFRAnalyzer()

    # Map our POS labels to cefrpy Penn Treebank tags
    POS_TO_CEFRPY = {
        "noun": "NN",
        "verb": "VB",
        "adjective": "JJ",
        "adverb": "RB",
    }

    def _get_cefr_level(self, word: str, pos: str) -> str:
        """Get CEFR level from cefrpy, using POS-specific level when available."""
        word_lower = word.lower()
        if not self._cefr_analyzer.is_word_in_database(word_lower):
            return ""

        # Try POS-specific level first
        cefrpy_pos = self.POS_TO_CEFRPY.get(pos)
        if cefrpy_pos:
            level = self._cefr_analyzer.get_word_pos_level_CEFR(word_lower, cefrpy_pos)
            if level:
                return level

        # Fallback to average level
        return self._cefr_analyzer.get_average_word_level_CEFR(word_lower) or ""

    @property
    def llm_service(self) -> LLMService:
        """Get or create LLM service instance."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _parse_json_array_response(self, response: str, module_id: int, book_id: str) -> list[dict[str, Any]]:
        """
        Parse JSON array from LLM response.

        Args:
            response: Raw LLM response text.
            module_id: Module ID for error context.
            book_id: Book ID for error context.

        Returns:
            Parsed JSON array.

        Raises:
            InvalidLLMResponseError: If JSON parsing fails.
        """
        # Clean up response - remove markdown code blocks
        cleaned = response.strip()

        # Remove markdown code blocks like ```json ... ``` or ``` ... ```
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

        # Try direct parse first (cleanest case)
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

        # Try to extract JSON array from response
        json_match = re.search(r"\[[\s\S]*?\](?=\s*$|\s*```)", cleaned)
        if not json_match:
            # Try more aggressive match
            json_match = re.search(r"\[[\s\S]*\]", cleaned)

        if not json_match:
            raise InvalidLLMResponseError(
                book_id=book_id,
                module_id=module_id,
                response=response[:500],
                parse_error="No JSON array found in response",
            )

        json_str = json_match.group(0)
        try:
            result = json.loads(json_str)
            if not isinstance(result, list):
                raise InvalidLLMResponseError(
                    book_id=book_id,
                    module_id=module_id,
                    response=response[:500],
                    parse_error="Response is not a JSON array",
                )
            return result
        except json.JSONDecodeError as e:
            raise InvalidLLMResponseError(
                book_id=book_id,
                module_id=module_id,
                response=response[:500],
                parse_error=str(e),
            ) from e

    def _extract_vocabulary_words(
        self,
        parsed: list[dict[str, Any]],
        module_id: int,
        max_words: int,
        min_word_length: int,
    ) -> list[VocabularyWord]:
        """
        Extract VocabularyWord objects from parsed JSON response.

        Args:
            parsed: Parsed JSON array.
            module_id: Module ID for context.
            max_words: Maximum number of words to include.
            min_word_length: Minimum word length to include.

        Returns:
            List of VocabularyWord objects.
        """
        words: list[VocabularyWord] = []

        for item in parsed[:max_words]:
            if not isinstance(item, dict):
                continue

            word = str(item.get("word", "")).strip()
            if not word or len(word) < min_word_length:
                continue

            # Validate part of speech
            pos = str(item.get("part_of_speech", "")).lower()
            valid_pos = [
                "noun",
                "verb",
                "adjective",
                "adverb",
                "pronoun",
                "preposition",
                "conjunction",
                "interjection",
                "article",
                "determiner",
            ]
            if pos not in valid_pos:
                pos = ""

            # Get CEFR level from cefrpy (reliable), fallback to LLM
            level = self._get_cefr_level(word, pos)
            if not level:
                level = str(item.get("level", "")).upper()
                if level not in ["A1", "A2", "B1", "B2", "C1", "C2"]:
                    level = ""

            vocab_word = VocabularyWord(
                word=word,
                translation=str(item.get("translation", "")),
                definition=str(item.get("definition", "")),
                part_of_speech=pos,
                level=level,
                example=str(item.get("example", "")),
                module_id=module_id,
                page=int(item.get("page", 0)) if item.get("page") else 0,
            )
            words.append(vocab_word)

        return words

    def _deduplicate_vocabulary(self, all_words: list[VocabularyWord]) -> list[VocabularyWord]:
        """
        Deduplicate vocabulary words across modules.

        Keeps the first occurrence of each word (case-insensitive).

        Args:
            all_words: List of all vocabulary words from all modules.

        Returns:
            Deduplicated list of vocabulary words.
        """
        seen: dict[str, VocabularyWord] = {}

        for word in all_words:
            # Use lowercase word as key for case-insensitive matching
            key = word.word.lower()
            if key not in seen:
                seen[key] = word

        # Return words in order of first occurrence
        return list(seen.values())

    async def extract_module_vocabulary(
        self,
        module_id: int,
        module_title: str,
        module_text: str,
        book_id: str,
        difficulty: str = "B1",
        language: str = "en",
    ) -> ModuleVocabularyResult:
        """
        Extract vocabulary from a single module's text content.

        Args:
            module_id: Module identifier.
            module_title: Module title.
            module_text: Text content of the module.
            book_id: Book identifier for error context.
            difficulty: Target CEFR difficulty level.
            language: Primary language of the content.

        Returns:
            ModuleVocabularyResult with extracted vocabulary.
        """
        logger.info(
            "Extracting vocabulary from module %d: %s (book: %s)",
            module_id,
            module_title,
            book_id,
        )

        # Handle empty or minimal text
        if not module_text or len(module_text.strip()) < 50:
            logger.warning(
                "Module %d has insufficient text (%d chars), returning empty result",
                module_id,
                len(module_text) if module_text else 0,
            )
            return ModuleVocabularyResult(
                module_id=module_id,
                module_title=module_title,
                words=[],
                success=True,
                error_message="Insufficient text for vocabulary extraction",
            )

        max_text = self.settings.vocabulary_max_text_length
        max_words = self.settings.vocabulary_max_words_per_module
        min_word_length = self.settings.vocabulary_min_word_length
        temperature = self.settings.vocabulary_temperature

        # Try main prompt first
        prompt = build_vocabulary_extraction_prompt(
            module_text,
            module_title=module_title,
            difficulty=difficulty,
            max_words=max_words,
            max_length=max_text,
            language=language,
        )
        provider_name = ""
        tokens_used = 0

        try:
            response = await self.llm_service.simple_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=temperature,
                max_tokens=2048,
            )
            provider_name = (
                self.llm_service.primary_provider.provider_name if self.llm_service.primary_provider else "unknown"
            )

            # Parse response
            parsed = self._parse_json_array_response(response, module_id, book_id)
            words = self._extract_vocabulary_words(parsed, module_id, max_words, min_word_length)

            logger.info(
                "Module %d vocabulary extracted: %d words",
                module_id,
                len(words),
            )

            return ModuleVocabularyResult(
                module_id=module_id,
                module_title=module_title,
                words=words,
                llm_provider=provider_name,
                tokens_used=tokens_used,
                success=True,
            )

        except InvalidLLMResponseError:
            # Try simpler prompt as fallback
            logger.warning(
                "Main prompt failed for module %d, trying simple prompt",
                module_id,
            )
            try:
                simple_prompt = build_simple_vocabulary_prompt(
                    module_text,
                    module_title=module_title,
                    max_words=max_words // 2,
                    max_length=max_text // 2,
                    language=language,
                )
                response = await self.llm_service.simple_completion(
                    prompt=simple_prompt,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=temperature,
                    max_tokens=1024,
                )

                parsed = self._parse_json_array_response(response, module_id, book_id)
                words = self._extract_vocabulary_words(parsed, module_id, max_words, min_word_length)

                return ModuleVocabularyResult(
                    module_id=module_id,
                    module_title=module_title,
                    words=words,
                    llm_provider=provider_name,
                    tokens_used=tokens_used,
                    success=True,
                )

            except Exception as fallback_error:
                logger.error(
                    "Fallback prompt also failed for module %d: %s",
                    module_id,
                    fallback_error,
                )
                return ModuleVocabularyResult(
                    module_id=module_id,
                    module_title=module_title,
                    words=[],
                    llm_provider=provider_name,
                    success=False,
                    error_message=f"Both prompts failed: {fallback_error}",
                )

        except LLMProviderError as e:
            logger.error(
                "LLM provider error for module %d: %s",
                module_id,
                e,
            )
            return ModuleVocabularyResult(
                module_id=module_id,
                module_title=module_title,
                words=[],
                llm_provider=provider_name,
                success=False,
                error_message=f"LLM provider error: {e}",
            )

        except Exception as e:
            logger.error(
                "Unexpected error extracting vocabulary from module %d: %s",
                module_id,
                e,
            )
            return ModuleVocabularyResult(
                module_id=module_id,
                module_title=module_title,
                words=[],
                llm_provider=provider_name,
                success=False,
                error_message=f"Unexpected error: {e}",
            )

    async def extract_book_vocabulary(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        modules: list[dict[str, Any]],
        language: str = "en",
        translation_language: str = "tr",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BookVocabularyResult:
        """
        Extract vocabulary from all modules for a book.

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher slug.
            book_name: Book folder name.
            modules: List of module dictionaries with module_id, title, text, difficulty.
            language: Primary language of the content.
            translation_language: Translation language.
            progress_callback: Optional callback(current, total) for progress.

        Returns:
            BookVocabularyResult with all vocabulary and module results.

        Raises:
            NoModulesFoundError: If no modules are provided.
        """
        if not modules:
            raise NoModulesFoundError(
                book_id=book_id,
                path=f"{publisher_slug}/books/{book_name}/ai-data/modules/",
            )

        logger.info(
            "Starting vocabulary extraction for book %s with %d modules",
            book_id,
            len(modules),
        )

        module_results: list[ModuleVocabularyResult] = []
        all_words: list[VocabularyWord] = []
        total = len(modules)

        for i, module_data in enumerate(modules):
            module_id = module_data.get("module_id", i + 1)
            module_title = module_data.get("title", f"Module {module_id}")
            module_text = module_data.get("text", "")
            difficulty = module_data.get("difficulty", "B1")

            result = await self.extract_module_vocabulary(
                module_id=module_id,
                module_title=module_title,
                module_text=module_text,
                book_id=book_id,
                difficulty=difficulty,
                language=language,
            )
            module_results.append(result)
            all_words.extend(result.words)

            if progress_callback:
                progress_callback(i + 1, total)

        # Deduplicate vocabulary across all modules
        deduplicated_words = self._deduplicate_vocabulary(all_words)

        book_result = BookVocabularyResult(
            book_id=book_id,
            publisher_id=publisher_slug,
            book_name=book_name,
            language=language,
            translation_language=translation_language,
            words=deduplicated_words,
            module_results=module_results,
            extracted_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Book vocabulary extraction complete: %d/%d modules succeeded, %d unique words (from %d total)",
            book_result.success_count,
            len(modules),
            len(deduplicated_words),
            len(all_words),
        )

        return book_result


# Singleton instance
_vocabulary_extraction_service: VocabularyExtractionService | None = None


def get_vocabulary_extraction_service() -> VocabularyExtractionService:
    """Get or create the global vocabulary extraction service instance."""
    global _vocabulary_extraction_service
    if _vocabulary_extraction_service is None:
        _vocabulary_extraction_service = VocabularyExtractionService()
    return _vocabulary_extraction_service
