"""Topic analysis service for extracting topics from module content."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from app.core.config import get_settings
from app.services.llm import LLMProviderError, get_llm_service
from app.services.topic_analysis.models import (
    BookAnalysisResult,
    InvalidLLMResponseError,
    ModuleAnalysisResult,
    NoModulesFoundError,
    TopicResult,
)
from app.services.topic_analysis.prompts import (
    SYSTEM_PROMPT,
    build_simple_topic_prompt,
    build_topic_extraction_prompt,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.llm.service import LLMService

logger = logging.getLogger(__name__)


class TopicAnalysisService:
    """
    Service for analyzing module content and extracting topics.

    Features:
    - Extract topics, grammar points, difficulty, and language from module text
    - Use LLM for intelligent content analysis
    - Handle LLM failures gracefully with fallback strategies
    - Support for multi-module book analysis
    """

    def __init__(
        self,
        settings: Settings | None = None,
        llm_service: LLMService | None = None,
    ) -> None:
        """
        Initialize topic analysis service.

        Args:
            settings: Application settings.
            llm_service: LLM service for AI analysis.
        """
        self.settings = settings or get_settings()
        self._llm_service = llm_service

    @property
    def llm_service(self) -> LLMService:
        """Get or create LLM service instance."""
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _parse_json_response(self, response: str, module_id: int, book_id: str) -> dict[str, Any]:
        """
        Parse JSON from LLM response.

        Args:
            response: Raw LLM response text.
            module_id: Module ID for error context.
            book_id: Book ID for error context.

        Returns:
            Parsed JSON dictionary.

        Raises:
            InvalidLLMResponseError: If JSON parsing fails.
        """
        # Try to extract JSON from response
        # LLM might include markdown code blocks or extra text
        json_match = re.search(r"\{[\s\S]*\}", response)
        if not json_match:
            raise InvalidLLMResponseError(
                book_id=book_id,
                module_id=module_id,
                response=response,
                parse_error="No JSON object found in response",
            )

        json_str = json_match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            raise InvalidLLMResponseError(
                book_id=book_id,
                module_id=module_id,
                response=response,
                parse_error=str(e),
            ) from e

    def _extract_topic_result(self, parsed: dict[str, Any], max_topics: int, max_grammar_points: int) -> TopicResult:
        """
        Extract TopicResult from parsed JSON response.

        Args:
            parsed: Parsed JSON dictionary.
            max_topics: Maximum number of topics to include.
            max_grammar_points: Maximum number of grammar points to include.

        Returns:
            TopicResult with extracted data.
        """
        topics = parsed.get("topics", [])
        if isinstance(topics, list):
            topics = [str(t) for t in topics[:max_topics]]
        else:
            topics = []

        grammar_points = parsed.get("grammar_points", [])
        if isinstance(grammar_points, list):
            grammar_points = [str(g) for g in grammar_points[:max_grammar_points]]
        else:
            grammar_points = []

        target_skills = parsed.get("target_skills", [])
        if isinstance(target_skills, list):
            target_skills = [str(s) for s in target_skills]
        else:
            target_skills = []

        difficulty = str(parsed.get("difficulty", "")).upper()
        if difficulty not in ["A1", "A2", "B1", "B2", "C1", "C2"]:
            difficulty = ""

        language = str(parsed.get("language", "")).lower()
        if language not in ["en", "tr", "bilingual"]:
            language = ""

        return TopicResult(
            topics=topics,
            grammar_points=grammar_points,
            difficulty=difficulty,
            language=language,
            target_skills=target_skills,
        )

    async def analyze_module(
        self,
        module_id: int,
        module_title: str,
        module_text: str,
        book_id: str,
    ) -> ModuleAnalysisResult:
        """
        Analyze a single module's text content.

        Args:
            module_id: Module identifier.
            module_title: Module title.
            module_text: Text content of the module.
            book_id: Book identifier for error context.

        Returns:
            ModuleAnalysisResult with extracted topics and metadata.
        """
        logger.info(
            "Analyzing module %d: %s (book: %s)",
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
            return ModuleAnalysisResult(
                module_id=module_id,
                module_title=module_title,
                topic_result=TopicResult.empty(),
                success=True,
                error_message="Insufficient text for analysis",
            )

        max_text = self.settings.topic_analysis_max_text_length
        max_topics = self.settings.topic_analysis_max_topics
        max_grammar = self.settings.topic_analysis_max_grammar_points
        temperature = self.settings.topic_analysis_temperature

        # Try main prompt first
        prompt = build_topic_extraction_prompt(module_text, max_length=max_text)
        provider_name = ""
        tokens_used = 0

        try:
            response = await self.llm_service.simple_completion(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
                temperature=temperature,
                max_tokens=1024,
            )
            provider_name = (
                self.llm_service.primary_provider.provider_name if self.llm_service.primary_provider else "unknown"
            )

            # Parse response
            parsed = self._parse_json_response(response, module_id, book_id)
            topic_result = self._extract_topic_result(parsed, max_topics, max_grammar)

            logger.info(
                "Module %d analyzed: %d topics, %d grammar points, difficulty=%s, language=%s",
                module_id,
                len(topic_result.topics),
                len(topic_result.grammar_points),
                topic_result.difficulty,
                topic_result.language,
            )

            return ModuleAnalysisResult(
                module_id=module_id,
                module_title=module_title,
                topic_result=topic_result,
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
                simple_prompt = build_simple_topic_prompt(module_text, max_length=max_text // 2)
                response = await self.llm_service.simple_completion(
                    prompt=simple_prompt,
                    system_prompt=SYSTEM_PROMPT,
                    temperature=temperature,
                    max_tokens=512,
                )

                parsed = self._parse_json_response(response, module_id, book_id)
                topic_result = self._extract_topic_result(parsed, max_topics, max_grammar)

                return ModuleAnalysisResult(
                    module_id=module_id,
                    module_title=module_title,
                    topic_result=topic_result,
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
                return ModuleAnalysisResult(
                    module_id=module_id,
                    module_title=module_title,
                    topic_result=TopicResult.empty(),
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
            return ModuleAnalysisResult(
                module_id=module_id,
                module_title=module_title,
                topic_result=TopicResult.empty(),
                llm_provider=provider_name,
                success=False,
                error_message=f"LLM provider error: {e}",
            )

        except Exception as e:
            logger.error(
                "Unexpected error analyzing module %d: %s",
                module_id,
                e,
            )
            return ModuleAnalysisResult(
                module_id=module_id,
                module_title=module_title,
                topic_result=TopicResult.empty(),
                llm_provider=provider_name,
                success=False,
                error_message=f"Unexpected error: {e}",
            )

    async def analyze_book(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        modules: list[dict[str, Any]],
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> BookAnalysisResult:
        """
        Analyze all modules for a book.

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher identifier.
            book_name: Book folder name.
            modules: List of module dictionaries with module_id, title, text.
            progress_callback: Optional callback(current, total) for progress.

        Returns:
            BookAnalysisResult with all module analysis results.

        Raises:
            NoModulesFoundError: If no modules are provided.
        """
        if not modules:
            raise NoModulesFoundError(
                book_id=book_id,
                path=f"{publisher_slug}/books/{book_name}/ai-data/modules/",
            )

        logger.info(
            "Starting topic analysis for book %s with %d modules",
            book_id,
            len(modules),
        )

        module_results: list[ModuleAnalysisResult] = []
        total = len(modules)

        for i, module_data in enumerate(modules):
            module_id = module_data.get("module_id", i + 1)
            module_title = module_data.get("title", f"Module {module_id}")
            module_text = module_data.get("text", "")

            result = await self.analyze_module(
                module_id=module_id,
                module_title=module_title,
                module_text=module_text,
                book_id=book_id,
            )
            module_results.append(result)

            if progress_callback:
                progress_callback(i + 1, total)

        book_result = BookAnalysisResult(
            book_id=book_id,
            publisher_id=publisher_slug,
            book_name=book_name,
            module_results=module_results,
            analyzed_at=datetime.now(timezone.utc),
        )

        logger.info(
            "Book analysis complete: %d/%d modules succeeded, primary_language=%s, difficulty_range=%s",
            book_result.success_count,
            len(modules),
            book_result.primary_language,
            book_result.difficulty_range,
        )

        return book_result


# Singleton instance
_topic_analysis_service: TopicAnalysisService | None = None


def get_topic_analysis_service() -> TopicAnalysisService:
    """Get or create the global topic analysis service instance."""
    global _topic_analysis_service
    if _topic_analysis_service is None:
        _topic_analysis_service = TopicAnalysisService()
    return _topic_analysis_service
