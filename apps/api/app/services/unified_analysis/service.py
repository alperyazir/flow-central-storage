"""Unified AI analysis service for combined module/topic/vocabulary extraction.

Supports two approaches:
1. Single-call approach: One LLM call for all modules + vocabulary (legacy)
2. Chunked approach: Phase 1 detects modules, Phase 2 extracts vocabulary per module (recommended)
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable

from app.core.config import get_settings
from app.services.unified_analysis.models import (
    AnalyzedModule,
    ChunkedProgress,
    UnifiedAnalysisResult,
    VocabularyWord,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.llm import LLMService

logger = logging.getLogger(__name__)


UNIFIED_ANALYSIS_PROMPT = """Analyze this educational book content and provide a COMPLETE analysis covering ALL pages.

## Your Task
1. **Identify ALL Module/Chapter Boundaries**: Find EVERY logical division in the content (units, chapters, lessons). Educational books typically have 6-15 units. Look for:
   - Unit/Chapter headers (e.g., "Unit 1", "Chapter 2", "Lesson 3")
   - Topic changes (new themes, new subjects)
   - Section breaks or numbered divisions
   - Each unit should typically be 5-15 pages

2. **Extract Topics**: For each module, identify the main educational topics and grammar points

3. **Extract Vocabulary**: For each module, extract 15-40 important vocabulary words that students are expected to learn in that module. These are the KEY words being taught — not common filler words.

## Content with Page Markers
{text_content}

## CRITICAL Requirements
- First detect the language of the book content (e.g., en, de, fr, es, etc.)
- Analyze the ENTIRE book from first page to last page
- Identify ALL modules/units throughout the book (typically 6-15 modules)
- NO module should exceed 20 pages - if you find a large section, break it into multiple modules
- Extract 15-40 vocabulary words per module (more for larger modules)
- Extract words IN THE LANGUAGE THEY APPEAR in the book (do NOT translate words to English)
- Include nouns, verbs, adjectives, prepositions, common phrases
- Provide Turkish translations for ALL vocabulary words
- The "definition" field should be a clear explanation in the book's own language
- Assess difficulty level (A1, A2, B1, B2, C1, C2)

## Response Format
Return ONLY a valid JSON object (no markdown, no explanations):
{{
  "language": "detected language code (en, de, fr, es, etc.)",
  "modules": [
    {{
      "title": "Module/Unit Title",
      "start_page": 1,
      "end_page": 10,
      "topics": ["Topic 1", "Topic 2"],
      "grammar_points": ["Grammar point 1"],
      "difficulty_level": "A1",
      "vocabulary": [
        {{
          "word": "example word in the book's language",
          "definition": "clear explanation in the book's language",
          "translation": "Turkish translation (Türkçe çeviri)",
          "part_of_speech": "noun",
          "example_sentence": "An example sentence from or inspired by the book content.",
          "difficulty": "A1"
        }}
      ]
    }}
  ]
}}

IMPORTANT: Ensure you cover ALL pages and identify ALL units. Do not stop at the first few units.

Analyze the content now:"""


# Chunked approach prompts
PHASE1_DETECT_MODULES_PROMPT = """Analyze this educational book and identify ALL modules/units/chapters.

## Your Task
Identify every logical division in this book. Look for:
- Unit/Chapter headers (e.g., "Unit 1", "Chapter 2", "Lesson 3")
- Topic changes and section breaks
- Educational books typically have 6-15 units

## Content with Page Markers
{text_content}

## Response Format
Return ONLY a valid JSON object:
{{
  "language": "detected language code (e.g., en, de, fr)",
  "total_pages": {total_pages},
  "modules": [
    {{
      "module_number": 1,
      "title": "Module/Unit Title",
      "start_page": 1,
      "end_page": 10,
      "topics": ["Main topic 1", "Main topic 2"],
      "difficulty_level": "A1"
    }}
  ]
}}

IMPORTANT:
- Cover ALL pages from start to end
- Identify ALL units"""


PHASE2_EXTRACT_VOCABULARY_PROMPT = """Extract vocabulary and write a brief summary for this educational content.

## Module Information
- Title: {module_title}
- Pages: {start_page} to {end_page}
- Topics: {topics}
- Level: {difficulty_level}
- Content Language: {language}

## Content
{module_text}

## Task
1. Write a 2-3 sentence summary describing what this module covers and its learning objectives.
2. Identify the key grammar points taught or practiced in this module (e.g., "Present Simple", "Comparatives", "Modal verbs: can/could").
3. Extract important vocabulary words that this module is teaching to students. Focus on the KEY words being introduced and practiced — not common filler words.

## Response Format
Return ONLY valid JSON:
{{
  "module_title": "{module_title}",
  "summary": "A 2-3 sentence summary of the module content and learning objectives.",
  "grammar_points": ["Grammar point 1", "Grammar point 2"],
  "vocabulary": [
    {{
      "word": "word in the content's language",
      "definition": "clear explanation in the content's language",
      "translation": "Turkish translation (Türkçe çeviri)",
      "part_of_speech": "noun",
      "difficulty": "A1"
    }}
  ]
}}

Focus on:
- Key nouns, verbs, adjectives that the module is teaching
- Common phrases and expressions being introduced
- Words essential to the module's topics
- Keep the "word" in its original language as it appears in the text
- Provide Turkish translations for every word"""


class UnifiedAnalysisService:
    """
    Unified AI analysis service that combines segmentation, topic analysis,
    and vocabulary extraction into a single LLM call.

    Benefits:
    - Lower API costs (1 call instead of N×2 calls)
    - Better accuracy (LLM sees full context)
    - More coherent results (vocabulary matches module themes)
    - Faster processing (fewer round trips)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        """
        Initialize unified analysis service.

        Args:
            settings: Application settings.
            llm_service: LLM service for API calls.
        """
        self.settings = settings or get_settings()
        self._llm_service = llm_service

    @property
    def llm_service(self) -> LLMService:
        """Get LLM service (lazy load)."""
        if self._llm_service is None:
            from app.services.llm import get_llm_service

            self._llm_service = get_llm_service()
        return self._llm_service

    async def analyze_book(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        pages: dict[int, str],
        translation_language: str = "tr",
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> UnifiedAnalysisResult:
        """
        Perform unified analysis on book content.

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher identifier.
            book_name: Book folder name.
            pages: Dictionary mapping page numbers to text content.
            translation_language: Target language for translations.
            progress_callback: Optional progress callback (current, total).

        Returns:
            UnifiedAnalysisResult with all analysis data.
        """
        start_time = time.time()
        total_pages = max(pages.keys()) if pages else 0

        logger.info(
            "Starting unified analysis for book %s (%d pages)",
            book_id,
            total_pages,
        )

        if progress_callback:
            progress_callback(0, 100)

        # Prepare text content with page markers
        text_content = self._prepare_text_content(pages)

        if progress_callback:
            progress_callback(10, 100)

        # Build prompt
        prompt = UNIFIED_ANALYSIS_PROMPT.format(text_content=text_content)

        if progress_callback:
            progress_callback(20, 100)

        # Call LLM
        try:
            response = await self.llm_service.simple_completion(
                prompt=prompt,
                system_prompt=(
                    "You are an expert educational content analyzer specializing in language learning books. "
                    "Analyze the ENTIRE book content to identify ALL modules, topics, and vocabulary. "
                    "Return only valid JSON, no markdown or explanations. "
                    "Be thorough - extract comprehensive vocabulary for each module."
                ),
                temperature=0.3,
                max_tokens=16000,  # Allow large response for comprehensive analysis
            )

            if progress_callback:
                progress_callback(70, 100)

            # Parse response
            analysis_data = self._parse_response(response)

            if progress_callback:
                progress_callback(80, 100)

            # Build result
            result = self._build_result(
                book_id=book_id,
                publisher_slug=publisher_slug,
                book_name=book_name,
                total_pages=total_pages,
                pages=pages,
                analysis_data=analysis_data,
                translation_language=translation_language,
            )

            # Add timing info
            result.processing_time_seconds = time.time() - start_time
            result.llm_model = self.llm_service.primary_provider.default_model

            if progress_callback:
                progress_callback(100, 100)

            logger.info(
                "Unified analysis completed: %d modules, %d vocabulary words, %.2fs",
                result.module_count,
                result.total_vocabulary,
                result.processing_time_seconds,
            )

            return result

        except Exception as e:
            logger.error("Unified analysis failed: %s", e)
            raise

    async def analyze_book_chunked(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        pages: dict[int, str],
        translation_language: str = "tr",
        progress_callback: Callable[[int, int], None] | None = None,
        detailed_progress_callback: Callable[[ChunkedProgress], None] | None = None,
        max_retries: int = 3,
    ) -> UnifiedAnalysisResult:
        """
        Perform chunked analysis on book content using two-phase approach.

        Phase 1: Detect all modules/chapters (structure only)
        Phase 2: Extract vocabulary for each module separately (with retries)

        This approach is more reliable for large books as each LLM call is smaller.

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher identifier.
            book_name: Book folder name.
            pages: Dictionary mapping page numbers to text content.
            translation_language: Target language for translations.
            progress_callback: Optional progress callback (current, total).
            detailed_progress_callback: Optional detailed progress callback.
            max_retries: Maximum retries per module for vocabulary extraction.

        Returns:
            UnifiedAnalysisResult with all analysis data.
        """
        start_time = time.time()
        total_pages = max(pages.keys()) if pages else 0

        logger.info(
            "Starting chunked analysis for book %s (%d pages)",
            book_id,
            total_pages,
        )

        # Prepare text content
        text_content = self._prepare_text_content(pages)

        # Report initial progress
        if progress_callback:
            progress_callback(5, 100)
        if detailed_progress_callback:
            detailed_progress_callback(
                ChunkedProgress(
                    phase="detecting_modules",
                    overall_percent=5,
                )
            )

        # ============ Phase 1: Detect Modules ============
        modules_data = await self._phase1_detect_modules(
            text_content=text_content,
            total_pages=total_pages,
            max_retries=max_retries,
        )

        if progress_callback:
            progress_callback(20, 100)

        detected_modules = modules_data.get("modules", [])
        primary_language = modules_data.get("language", "en")

        if not detected_modules:
            raise ValueError("No modules detected in Phase 1")

        logger.info(
            "Phase 1 complete: detected %d modules, language=%s",
            len(detected_modules),
            primary_language,
        )

        # ============ Phase 2: Extract Vocabulary Per Module ============
        modules: list[AnalyzedModule] = []
        difficulty_levels: set[str] = set()
        all_vocabulary: list[VocabularyWord] = []

        for i, mod_data in enumerate(detected_modules):
            module_progress = ChunkedProgress(
                phase="extracting_vocabulary",
                current_module=i + 1,
                total_modules=len(detected_modules),
                module_title=mod_data.get("title", f"Module {i + 1}"),
                overall_percent=20 + int((i / len(detected_modules)) * 70),
            )

            if detailed_progress_callback:
                detailed_progress_callback(module_progress)
            if progress_callback:
                progress_callback(module_progress.overall_percent, 100)

            start_page = mod_data.get("start_page", 1)
            end_page = mod_data.get("end_page", total_pages)
            difficulty = mod_data.get("difficulty_level", "intermediate")
            difficulty_levels.add(difficulty)

            # Get module text
            module_pages = list(range(start_page, end_page + 1))
            module_text = self._get_pages_text(pages, start_page, end_page)

            # Extract vocabulary, summary, and grammar points with retries
            vocabulary: list[VocabularyWord] = []
            summary = ""
            grammar_points: list[str] = []
            for attempt in range(max_retries):
                try:
                    vocab_data = await self._phase2_extract_vocabulary(
                        module_title=mod_data.get("title", f"Module {i + 1}"),
                        start_page=start_page,
                        end_page=end_page,
                        topics=mod_data.get("topics", []),
                        difficulty_level=difficulty,
                        module_text=module_text,
                        language=primary_language,
                    )
                    vocabulary = self._parse_vocabulary(vocab_data)
                    summary = vocab_data.get("summary", "")
                    grammar_points = vocab_data.get("grammar_points", [])
                    break
                except Exception as e:
                    module_progress.retry_count = attempt + 1
                    if detailed_progress_callback:
                        detailed_progress_callback(module_progress)

                    if attempt < max_retries - 1:
                        logger.warning(
                            "Vocabulary extraction failed for module %d (attempt %d/%d): %s",
                            i + 1,
                            attempt + 1,
                            max_retries,
                            e,
                        )
                    else:
                        logger.error(
                            "Vocabulary extraction failed for module %d after %d attempts: %s", i + 1, max_retries, e
                        )
                        # Continue with empty vocabulary for this module

            all_vocabulary.extend(vocabulary)

            # Build module
            module = AnalyzedModule(
                module_id=i + 1,
                title=mod_data.get("title", f"Module {i + 1}"),
                start_page=start_page,
                end_page=end_page,
                pages=module_pages,
                text=module_text,
                topics=mod_data.get("topics", []),
                grammar_points=grammar_points,
                difficulty_level=difficulty,
                language=primary_language,
                summary=summary,
                vocabulary=vocabulary,
            )
            modules.append(module)

            logger.info(
                "Module %d/%d complete: %s - %d vocabulary words",
                i + 1,
                len(detected_modules),
                module.title,
                len(vocabulary),
            )

        # Build final result
        result = UnifiedAnalysisResult(
            book_id=book_id,
            publisher_slug=publisher_slug,
            book_name=book_name,
            total_pages=total_pages,
            modules=modules,
            primary_language=primary_language,
            translation_language=translation_language,
            difficulty_range=sorted(difficulty_levels),
            method="chunked_ai",
            processing_time_seconds=time.time() - start_time,
            llm_model=self.llm_service.primary_provider.default_model,
        )

        if progress_callback:
            progress_callback(100, 100)
        if detailed_progress_callback:
            detailed_progress_callback(
                ChunkedProgress(
                    phase="complete",
                    current_module=len(modules),
                    total_modules=len(modules),
                    overall_percent=100,
                )
            )

        logger.info(
            "Chunked analysis completed: %d modules, %d vocabulary words, %.2fs",
            result.module_count,
            result.total_vocabulary,
            result.processing_time_seconds,
        )

        return result

    async def _phase1_detect_modules(
        self,
        text_content: str,
        total_pages: int,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Phase 1: Detect all modules in the book."""
        prompt = PHASE1_DETECT_MODULES_PROMPT.format(
            text_content=text_content,
            total_pages=total_pages,
        )

        for attempt in range(max_retries):
            try:
                response = await self.llm_service.simple_completion(
                    prompt=prompt,
                    system_prompt=(
                        "You are an expert at analyzing educational book structure. "
                        "Identify logical divisions (units, chapters, modules). "
                        "Return only valid JSON, no markdown or explanations."
                    ),
                    temperature=0.2,
                    max_tokens=4000,
                )

                return self._parse_json_response(response)

            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning("Phase 1 failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
                else:
                    raise

        return {"modules": []}

    async def _phase2_extract_vocabulary(
        self,
        module_title: str,
        start_page: int,
        end_page: int,
        topics: list[str],
        difficulty_level: str,
        module_text: str,
        language: str = "en",
    ) -> dict[str, Any]:
        """Phase 2: Extract vocabulary for a single module."""
        prompt = PHASE2_EXTRACT_VOCABULARY_PROMPT.format(
            module_title=module_title,
            start_page=start_page,
            end_page=end_page,
            topics=", ".join(topics) if topics else "General",
            difficulty_level=difficulty_level,
            module_text=module_text[:50000],  # Limit text size
            language=language,
        )

        response = await self.llm_service.simple_completion(
            prompt=prompt,
            system_prompt=(
                "You are an expert vocabulary extractor for language learning. "
                "Extract important vocabulary with definitions and translations. "
                "Return only valid JSON, no markdown or explanations."
            ),
            temperature=0.3,
            max_tokens=4000,
        )

        return self._parse_json_response(response)

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling various formats."""
        response = response.strip()

        # Try to find JSON object in response
        json_start = response.find("{")
        json_end = response.rfind("}")

        if json_start != -1 and json_end != -1:
            json_str = response[json_start : json_end + 1]
        else:
            # Fallback: remove markdown code blocks
            if response.startswith("```json"):
                response = response[7:]
            if response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            json_str = response.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.error("Failed to parse JSON response: %s", e)
            logger.debug("Response was: %s", response[:500])
            raise ValueError(f"Invalid JSON response: {e}") from e

    def _get_pages_text(
        self,
        pages: dict[int, str],
        start_page: int,
        end_page: int,
    ) -> str:
        """Get concatenated text for a page range."""
        text_parts = []
        for page_num in range(start_page, end_page + 1):
            if page_num in pages:
                text_parts.append(f"--- Page {page_num} ---\n{pages[page_num]}")
        return "\n\n".join(text_parts)

    def _parse_vocabulary(self, vocab_data: dict[str, Any]) -> list[VocabularyWord]:
        """Parse vocabulary from response data."""
        vocabulary: list[VocabularyWord] = []
        for v in vocab_data.get("vocabulary", []):
            word = VocabularyWord(
                word=v.get("word", ""),
                definition=v.get("definition", ""),
                translation=v.get("translation", ""),
                part_of_speech=v.get("part_of_speech", ""),
                example_sentence=v.get("example_sentence", ""),
                difficulty=v.get("difficulty", "intermediate"),
                phonetic=v.get("phonetic", ""),
            )
            if word.word:
                vocabulary.append(word)
        return vocabulary

    def _prepare_text_content(
        self,
        pages: dict[int, str],
        max_chars_per_page: int = 1200,
        max_total_chars: int = 80000,
    ) -> str:
        """
        Prepare text content with page markers for LLM.

        Uses smart summarization for very long books while ensuring
        ALL pages are represented.
        """
        parts = []
        total_chars = 0
        total_pages = max(pages.keys()) if pages else 0

        # First pass: calculate if we need to truncate
        full_content_size = sum(len(pages.get(p, "")) for p in pages)

        # If content fits, include everything
        if full_content_size <= max_total_chars:
            for page_num in sorted(pages.keys()):
                text = pages.get(page_num, "").strip()
                if not text:
                    continue
                page_marker = f"\n--- Page {page_num} ---\n"
                parts.append(page_marker + text)
            return "".join(parts)

        # Otherwise, use smart truncation per page
        chars_per_page = max_total_chars // total_pages if total_pages > 0 else max_chars_per_page
        chars_per_page = max(200, min(chars_per_page, max_chars_per_page))

        for page_num in sorted(pages.keys()):
            text = pages.get(page_num, "").strip()
            if not text:
                continue

            # Truncate long pages but ensure ALL pages are included
            if len(text) > chars_per_page:
                text = text[:chars_per_page] + "..."

            page_marker = f"\n--- Page {page_num} ---\n"
            page_content = page_marker + text
            parts.append(page_content)
            total_chars += len(page_content)

        # Add note about page count
        if total_chars > max_total_chars:
            logger.warning(
                "Text content exceeds limit (%d > %d), some pages truncated",
                total_chars,
                max_total_chars,
            )

        return "".join(parts)

    def _parse_response(self, response: str) -> dict[str, Any]:
        """Parse LLM JSON response."""
        response = response.strip()

        # Remove markdown code blocks if present
        if response.startswith("```"):
            lines = response.split("\n")
            # Find end of code block
            end_idx = len(lines)
            for i in range(len(lines) - 1, 0, -1):
                if lines[i].strip() == "```":
                    end_idx = i
                    break
            response = "\n".join(lines[1:end_idx])
            response = response.strip()

        try:
            data = json.loads(response)
            return data
        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            logger.debug("Response was: %s", response[:500])
            raise ValueError(f"Invalid JSON response from LLM: {e}") from e

    def _build_result(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        total_pages: int,
        pages: dict[int, str],
        analysis_data: dict[str, Any],
        translation_language: str,
    ) -> UnifiedAnalysisResult:
        """Build UnifiedAnalysisResult from parsed LLM response."""
        modules_data = analysis_data.get("modules", [])
        primary_language = analysis_data.get("language", "en")

        modules: list[AnalyzedModule] = []
        difficulty_levels: set[str] = set()

        for i, mod_data in enumerate(modules_data):
            start_page = mod_data.get("start_page", 1)
            end_page = mod_data.get("end_page", total_pages)

            # Collect pages and text for this module
            module_pages = list(range(start_page, end_page + 1))
            text_parts = []
            for page_num in module_pages:
                if page_num in pages:
                    text_parts.append(pages[page_num])
            text = "\n\n".join(text_parts)

            # Parse vocabulary
            vocabulary: list[VocabularyWord] = []
            for vocab_data in mod_data.get("vocabulary", []):
                word = VocabularyWord(
                    word=vocab_data.get("word", ""),
                    definition=vocab_data.get("definition", ""),
                    translation=vocab_data.get("translation", ""),
                    part_of_speech=vocab_data.get("part_of_speech", ""),
                    example_sentence=vocab_data.get("example_sentence", ""),
                    difficulty=vocab_data.get("difficulty", "intermediate"),
                    phonetic=vocab_data.get("phonetic", ""),
                )
                if word.word:  # Only add if word is not empty
                    vocabulary.append(word)

            difficulty = mod_data.get("difficulty_level", "intermediate")
            difficulty_levels.add(difficulty)

            module = AnalyzedModule(
                module_id=i + 1,
                title=mod_data.get("title", f"Module {i + 1}"),
                start_page=start_page,
                end_page=end_page,
                pages=module_pages,
                text=text,
                topics=mod_data.get("topics", []),
                grammar_points=mod_data.get("grammar_points", []),
                difficulty_level=difficulty,
                language=primary_language,
                summary=mod_data.get("summary", ""),
                vocabulary=vocabulary,
            )
            modules.append(module)

        return UnifiedAnalysisResult(
            book_id=book_id,
            publisher_slug=publisher_slug,
            book_name=book_name,
            total_pages=total_pages,
            modules=modules,
            primary_language=primary_language,
            translation_language=translation_language,
            difficulty_range=sorted(difficulty_levels),
            method="unified_ai",
        )


# Singleton instance
_unified_analysis_service: UnifiedAnalysisService | None = None


def get_unified_analysis_service() -> UnifiedAnalysisService:
    """Get or create the global unified analysis service instance."""
    global _unified_analysis_service
    if _unified_analysis_service is None:
        _unified_analysis_service = UnifiedAnalysisService()
    return _unified_analysis_service
