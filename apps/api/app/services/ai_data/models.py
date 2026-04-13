"""AI data storage models and exceptions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# =============================================================================
# Enums
# =============================================================================


class ProcessingStatus(str, Enum):
    """Processing status values for metadata.json."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class StageStatus(str, Enum):
    """Stage status values."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# =============================================================================
# Exceptions
# =============================================================================


class AIDataStorageError(Exception):
    """Base exception for AI data storage errors."""

    def __init__(self, message: str, book_id: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.book_id = book_id
        self.details = details or {}
        super().__init__(f"[{book_id}] {message}")


class MetadataError(AIDataStorageError):
    """Raised when metadata operations fail."""

    def __init__(
        self,
        book_id: str,
        operation: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"Metadata {operation} failed: {reason}",
            book_id,
            {"operation": operation, "reason": reason},
        )
        self.operation = operation
        self.reason = reason


class InitializationError(AIDataStorageError):
    """Raised when AI data structure initialization fails."""

    def __init__(
        self,
        book_id: str,
        path: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"Failed to initialize ai-data structure at '{path}': {reason}",
            book_id,
            {"path": path, "reason": reason},
        )
        self.path = path
        self.reason = reason


class CleanupError(AIDataStorageError):
    """Raised when cleanup operations fail."""

    def __init__(
        self,
        book_id: str,
        path: str,
        reason: str,
    ) -> None:
        super().__init__(
            f"Cleanup failed for '{path}': {reason}",
            book_id,
            {"path": path, "reason": reason},
        )
        self.path = path
        self.reason = reason


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class StageResult:
    """Result of a processing stage."""

    status: StageStatus = StageStatus.PENDING
    completed_at: datetime | None = None
    error_message: str = ""
    # Stage-specific data stored as dict
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        result: dict[str, Any] = {
            "status": self.status.value,
        }
        if self.completed_at:
            result["completed_at"] = self.completed_at.isoformat()
        if self.error_message:
            result["error_message"] = self.error_message
        # Merge stage-specific data
        result.update(self.data)
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StageResult:
        """Create StageResult from dictionary."""
        status_str = data.get("status", "pending")
        status = StageStatus(status_str) if status_str else StageStatus.PENDING

        completed_at = data.get("completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)

        # Extract known fields, rest goes to data
        known_fields = {"status", "completed_at", "error_message"}
        stage_data = {k: v for k, v in data.items() if k not in known_fields}

        return cls(
            status=status,
            completed_at=completed_at,
            error_message=data.get("error_message", ""),
            data=stage_data,
        )


def _safe_int(value: object) -> int:
    """Parse value as int, return 0 if not numeric (handles legacy name-based publisher_id)."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


@dataclass
class ProcessingMetadata:
    """
    Metadata for AI processing of a book.

    This represents the consolidated metadata.json file that aggregates
    all processing information across stages.
    """

    book_id: str
    publisher_id: str | int
    book_name: str
    processing_status: ProcessingStatus = ProcessingStatus.PENDING
    processing_started_at: datetime | None = None
    processing_completed_at: datetime | None = None
    total_pages: int = 0
    total_modules: int = 0
    total_vocabulary: int = 0
    total_audio_files: int = 0
    languages: list[str] = field(default_factory=list)
    primary_language: str = ""
    difficulty_range: list[str] = field(default_factory=list)
    llm_provider: str = ""
    tts_provider: str = ""
    stages: dict[str, StageResult] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON storage."""
        result: dict[str, Any] = {
            "book_id": self.book_id,
            "publisher_id": self.publisher_id,
            "book_name": self.book_name,
            "processing_status": self.processing_status.value,
            "total_pages": self.total_pages,
            "total_modules": self.total_modules,
            "total_vocabulary": self.total_vocabulary,
            "total_audio_files": self.total_audio_files,
            "languages": self.languages,
            "primary_language": self.primary_language,
            "difficulty_range": self.difficulty_range,
            "llm_provider": self.llm_provider,
            "tts_provider": self.tts_provider,
            "stages": {name: stage.to_dict() for name, stage in self.stages.items()},
            "errors": self.errors,
        }

        if self.processing_started_at:
            result["processing_started_at"] = self.processing_started_at.isoformat()
        if self.processing_completed_at:
            result["processing_completed_at"] = self.processing_completed_at.isoformat()

        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessingMetadata:
        """Create ProcessingMetadata from dictionary."""
        status_str = data.get("processing_status", "pending")
        status = ProcessingStatus(status_str) if status_str else ProcessingStatus.PENDING

        started_at = data.get("processing_started_at")
        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at)

        completed_at = data.get("processing_completed_at")
        if isinstance(completed_at, str):
            completed_at = datetime.fromisoformat(completed_at)

        # Parse stages
        stages_data = data.get("stages", {})
        stages = {name: StageResult.from_dict(stage_data) for name, stage_data in stages_data.items()}

        return cls(
            book_id=data.get("book_id", ""),
            publisher_id=_safe_int(data.get("publisher_id", 0)),
            book_name=data.get("book_name", ""),
            processing_status=status,
            processing_started_at=started_at,
            processing_completed_at=completed_at,
            total_pages=data.get("total_pages", 0),
            total_modules=data.get("total_modules", 0),
            total_vocabulary=data.get("total_vocabulary", 0),
            total_audio_files=data.get("total_audio_files", 0),
            languages=data.get("languages", []),
            primary_language=data.get("primary_language", ""),
            difficulty_range=data.get("difficulty_range", []),
            llm_provider=data.get("llm_provider", ""),
            tts_provider=data.get("tts_provider", ""),
            stages=stages,
            errors=data.get("errors", []),
        )


@dataclass
class AIDataStructure:
    """Represents the ai-data directory structure paths."""

    base_path: str
    text_path: str
    modules_path: str
    vocabulary_path: str
    audio_path: str
    audio_vocabulary_path: str
    metadata_path: str

    @classmethod
    def from_book_info(cls, publisher_slug: str, book_id: str, book_name: str) -> AIDataStructure:
        """Create AIDataStructure from book information."""
        base = f"{publisher_slug}/books/{book_name}/ai-data"
        return cls(
            base_path=base,
            text_path=f"{base}/text",
            modules_path=f"{base}/modules",
            vocabulary_path=f"{base}/vocabulary.json",
            audio_path=f"{base}/audio",
            audio_vocabulary_path=f"{base}/audio/vocabulary",
            metadata_path=f"{base}/metadata.json",
        )

    def get_all_directories(self) -> list[str]:
        """Get all directory paths (not files)."""
        return [
            self.base_path,
            self.text_path,
            self.modules_path,
            self.audio_path,
            self.audio_vocabulary_path,
        ]

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary."""
        return {
            "base_path": self.base_path,
            "text_path": self.text_path,
            "modules_path": self.modules_path,
            "vocabulary_path": self.vocabulary_path,
            "audio_path": self.audio_path,
            "audio_vocabulary_path": self.audio_vocabulary_path,
            "metadata_path": self.metadata_path,
        }


@dataclass
class CleanupStats:
    """Statistics from cleanup operation."""

    total_deleted: int = 0
    text_deleted: int = 0
    modules_deleted: int = 0
    vocabulary_deleted: int = 0
    audio_deleted: int = 0
    metadata_deleted: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_deleted": self.total_deleted,
            "text_deleted": self.text_deleted,
            "modules_deleted": self.modules_deleted,
            "vocabulary_deleted": self.vocabulary_deleted,
            "audio_deleted": self.audio_deleted,
            "metadata_deleted": self.metadata_deleted,
            "errors": self.errors,
        }
