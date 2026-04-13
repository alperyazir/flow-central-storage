"""Data models for unified AI analysis service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class VocabularyWord:
    """A vocabulary word extracted from a module."""

    word: str
    definition: str
    translation: str
    part_of_speech: str = ""
    example_sentence: str = ""
    difficulty: str = "intermediate"  # beginner, intermediate, advanced
    phonetic: str = ""


@dataclass
class AnalyzedModule:
    """A module with all analysis results from unified LLM call."""

    module_id: int
    title: str
    start_page: int
    end_page: int
    pages: list[int] = field(default_factory=list)
    text: str = ""

    # Topic analysis results
    topics: list[str] = field(default_factory=list)
    grammar_points: list[str] = field(default_factory=list)
    difficulty_level: str = "intermediate"
    language: str = "en"
    summary: str = ""

    # Vocabulary results
    vocabulary: list[VocabularyWord] = field(default_factory=list)

    # Metadata
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def word_count(self) -> int:
        """Get word count from text."""
        return len(self.text.split()) if self.text else 0

    @property
    def page_count(self) -> int:
        """Get number of pages."""
        return len(self.pages)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "module_id": self.module_id,
            "title": self.title,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "pages": self.pages,
            "text": self.text,
            "word_count": self.word_count,
            "topics": self.topics,
            "grammar_points": self.grammar_points,
            "difficulty_level": self.difficulty_level,
            "language": self.language,
            "summary": self.summary,
            "vocabulary": [
                {
                    "word": v.word,
                    "definition": v.definition,
                    "translation": v.translation,
                    "part_of_speech": v.part_of_speech,
                    "example_sentence": v.example_sentence,
                    "difficulty": v.difficulty,
                    "phonetic": v.phonetic,
                }
                for v in self.vocabulary
            ],
            "extracted_at": self.extracted_at.isoformat() if self.extracted_at else None,
        }


@dataclass
class UnifiedAnalysisResult:
    """Complete result from unified AI analysis."""

    book_id: str
    publisher_slug: str
    book_name: str
    total_pages: int

    modules: list[AnalyzedModule] = field(default_factory=list)

    # Aggregated stats
    primary_language: str = "en"
    translation_language: str = "tr"
    difficulty_range: list[str] = field(default_factory=list)

    # Processing info
    method: str = "unified_ai"
    llm_model: str = ""
    processing_time_seconds: float = 0.0
    total_tokens_used: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def module_count(self) -> int:
        """Get number of modules."""
        return len(self.modules)

    @property
    def total_vocabulary(self) -> int:
        """Get total vocabulary word count."""
        return sum(len(m.vocabulary) for m in self.modules)

    @property
    def total_topics(self) -> int:
        """Get total topic count."""
        return sum(len(m.topics) for m in self.modules)

    @property
    def all_vocabulary(self) -> list[VocabularyWord]:
        """Get all vocabulary words from all modules."""
        vocab = []
        for module in self.modules:
            vocab.extend(module.vocabulary)
        return vocab

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "book_id": self.book_id,
            "publisher_slug": self.publisher_slug,
            "book_name": self.book_name,
            "total_pages": self.total_pages,
            "module_count": self.module_count,
            "total_vocabulary": self.total_vocabulary,
            "total_topics": self.total_topics,
            "primary_language": self.primary_language,
            "translation_language": self.translation_language,
            "difficulty_range": self.difficulty_range,
            "method": self.method,
            "llm_model": self.llm_model,
            "processing_time_seconds": self.processing_time_seconds,
            "total_tokens_used": self.total_tokens_used,
            "estimated_cost_usd": self.estimated_cost_usd,
            "modules": [m.to_dict() for m in self.modules],
        }


@dataclass
class ChunkedProgress:
    """Progress information for chunked analysis."""

    phase: str = "detecting_modules"  # detecting_modules, extracting_vocabulary
    current_module: int = 0
    total_modules: int = 0
    module_title: str = ""
    retry_count: int = 0
    overall_percent: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "phase": self.phase,
            "current_module": self.current_module,
            "total_modules": self.total_modules,
            "module_title": self.module_title,
            "retry_count": self.retry_count,
            "overall_percent": self.overall_percent,
        }
