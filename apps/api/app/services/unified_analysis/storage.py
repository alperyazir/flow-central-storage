"""Storage service for unified analysis results."""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING, Any

from cefrpy import CEFRAnalyzer

from app.core.config import get_settings
from app.services.unified_analysis.models import UnifiedAnalysisResult

_cefr_analyzer = CEFRAnalyzer()

_POS_TO_CEFRPY = {
    "noun": "NN",
    "verb": "VB",
    "adjective": "JJ",
    "adverb": "RB",
}


def _get_cefr_level(word: str, pos: str) -> str:
    """Get CEFR level from cefrpy, using POS-specific level when available."""
    word_lower = word.lower()
    if not _cefr_analyzer.is_word_in_database(word_lower):
        return ""

    cefrpy_pos = _POS_TO_CEFRPY.get(pos)
    if cefrpy_pos:
        level = _cefr_analyzer.get_word_pos_level_CEFR(word_lower, cefrpy_pos)
        if level:
            return level

    return _cefr_analyzer.get_average_word_level_CEFR(word_lower) or ""

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class UnifiedAnalysisStorage:
    """Storage service for saving unified analysis results to MinIO."""

    def __init__(self, settings: Settings | None = None) -> None:
        """Initialize storage service."""
        self.settings = settings or get_settings()

    def _get_minio_client(self):
        """Get MinIO client."""
        from app.services.minio import get_minio_client

        return get_minio_client(self.settings)

    def save_all(
        self,
        result: UnifiedAnalysisResult,
    ) -> dict[str, Any]:
        """
        Save all unified analysis results to storage.

        Saves:
        - modules/{module_id}.json for each module (compatible with existing format)
        - vocabulary.json (aggregated vocabulary)
        - analysis_metadata.json (processing info)

        Args:
            result: UnifiedAnalysisResult to save.

        Returns:
            Dict with saved file paths and counts.
        """
        client = self._get_minio_client()
        bucket = self.settings.minio_publishers_bucket
        base_path = f"{result.publisher_slug}/books/{result.book_name}/ai-data"

        saved_modules = []
        saved_vocab_count = 0

        # Save each module
        for module in result.modules:
            module_path = f"{base_path}/modules/module_{module.module_id}.json"
            module_data = module.to_dict()

            try:
                content = json.dumps(module_data, indent=2, ensure_ascii=False)
                content_bytes = content.encode("utf-8")
                client.put_object(
                    bucket,
                    module_path,
                    data=io.BytesIO(content_bytes),
                    length=len(content_bytes),
                    content_type="application/json",
                )
                saved_modules.append(module_path)
                saved_vocab_count += len(module.vocabulary)
                logger.debug("Saved module %d to %s", module.module_id, module_path)
            except Exception as e:
                logger.error("Failed to save module %d: %s", module.module_id, e)

        # Save aggregated vocabulary.json (compatible with existing format)
        vocab_path = f"{base_path}/vocabulary.json"

        # Build vocabulary words with unique IDs
        vocab_words = []
        word_counter = 0
        for module in result.modules:
            for v in module.vocabulary:
                word_counter += 1
                # Get CEFR level from cefrpy (reliable), fallback to LLM value
                level = _get_cefr_level(v.word, v.part_of_speech) or v.difficulty

                vocab_words.append(
                    {
                        "id": f"word_{word_counter}",
                        "word": v.word,
                        "definition": v.definition,
                        "translation": v.translation,
                        "part_of_speech": v.part_of_speech,
                        "example": v.example_sentence,
                        "level": level,
                        "phonetic": v.phonetic,
                        "module_id": module.module_id,
                        "module_title": module.title,
                    }
                )

        vocab_data = {
            "book_id": result.book_id,
            "publisher_id": result.publisher_slug,
            "book_name": result.book_name,
            "language": result.primary_language,
            "translation_language": result.translation_language,
            "total_words": result.total_vocabulary,
            "words": vocab_words,
        }

        try:
            content = json.dumps(vocab_data, indent=2, ensure_ascii=False)
            content_bytes = content.encode("utf-8")
            client.put_object(
                bucket,
                vocab_path,
                data=io.BytesIO(content_bytes),
                length=len(content_bytes),
                content_type="application/json",
            )
            logger.info("Saved vocabulary.json with %d words", result.total_vocabulary)
        except Exception as e:
            logger.error("Failed to save vocabulary.json: %s", e)

        # Save modules metadata (compatible with existing format)
        modules_meta_path = f"{base_path}/modules/metadata.json"
        modules_meta = {
            "book_id": result.book_id,
            "publisher_id": result.publisher_slug,
            "book_name": result.book_name,
            "total_pages": result.total_pages,
            "module_count": result.module_count,
            "method": result.method,
            "primary_language": result.primary_language,
            "difficulty_range": result.difficulty_range,
            "modules": [
                {
                    "module_id": m.module_id,
                    "title": m.title,
                    "start_page": m.start_page,
                    "end_page": m.end_page,
                    "page_count": m.page_count,
                    "word_count": m.word_count,
                    "topics": m.topics,
                    "difficulty_level": m.difficulty_level,
                    "summary": m.summary,
                    "vocabulary_count": len(m.vocabulary),
                }
                for m in result.modules
            ],
        }

        try:
            content = json.dumps(modules_meta, indent=2, ensure_ascii=False)
            content_bytes = content.encode("utf-8")
            client.put_object(
                bucket,
                modules_meta_path,
                data=io.BytesIO(content_bytes),
                length=len(content_bytes),
                content_type="application/json",
            )
            logger.info("Saved modules metadata.json")
        except Exception as e:
            logger.error("Failed to save modules metadata: %s", e)

        return {
            "modules": saved_modules,
            "vocabulary": vocab_path,
            "modules_metadata": modules_meta_path,
            "module_count": len(saved_modules),
            "vocabulary_count": saved_vocab_count,
        }


# Singleton instance
_unified_storage: UnifiedAnalysisStorage | None = None


def get_unified_analysis_storage() -> UnifiedAnalysisStorage:
    """Get or create the global unified analysis storage instance."""
    global _unified_storage
    if _unified_storage is None:
        _unified_storage = UnifiedAnalysisStorage()
    return _unified_storage
