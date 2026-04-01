"""Pydantic schemas for AI data retrieval API responses."""

from __future__ import annotations

from pydantic import BaseModel, Field

# =============================================================================
# Module Schemas
# =============================================================================


class ModuleSummary(BaseModel):
    """Summary of a module for list responses."""

    module_id: int = Field(..., description="Module identifier")
    title: str = Field(..., description="Module title")
    pages: list[int] = Field(default_factory=list, description="Page numbers in module")
    word_count: int = Field(0, description="Number of words in module")


class ModuleListResponse(BaseModel):
    """Response schema for listing modules."""

    book_id: str | int = Field(..., description="Book identifier")
    total_modules: int = Field(..., description="Total number of modules")
    modules: list[ModuleSummary] = Field(default_factory=list, description="List of module summaries")


class ModuleDetailResponse(BaseModel):
    """Response schema for full module data."""

    module_id: int = Field(..., description="Module identifier")
    title: str = Field(..., description="Module title")
    pages: list[int] = Field(default_factory=list, description="Page numbers in module")
    text: str = Field("", description="Full text content of the module")
    topics: list[str] = Field(default_factory=list, description="Topics covered in module")
    grammar_points: list[str] = Field(default_factory=list, description="Grammar points taught in module")
    vocabulary_ids: list[str] = Field(default_factory=list, description="Vocabulary word IDs in module")
    language: str = Field("", description="Primary language of module")
    summary: str = Field("", description="2-3 sentence summary of module content and learning objectives")
    difficulty: str = Field("", description="CEFR difficulty level")
    word_count: int = Field(0, description="Number of words in module")
    extracted_at: str | None = Field(None, description="When module was extracted")


class ModuleMetadataSummary(BaseModel):
    """Summary of a module in metadata response."""

    module_id: int = Field(..., description="Module identifier")
    title: str = Field(..., description="Module title")
    start_page: int = Field(..., description="Start page number")
    end_page: int = Field(..., description="End page number")
    page_count: int = Field(0, description="Number of pages in module")
    word_count: int = Field(0, description="Number of words in module")
    topics: list[str] = Field(default_factory=list, description="Topics covered in module")
    difficulty_level: str = Field("", description="CEFR difficulty level")
    summary: str = Field("", description="2-3 sentence summary of module content and learning objectives")
    vocabulary_count: int = Field(0, description="Number of vocabulary words")


class ModulesMetadataResponse(BaseModel):
    """Response schema for modules metadata.json."""

    book_id: str | int = Field(..., description="Book identifier")
    publisher_id: str | int = Field(..., description="Publisher identifier")
    book_name: str = Field(..., description="Book folder name")
    total_pages: int = Field(0, description="Total pages in book")
    module_count: int = Field(0, description="Total number of modules")
    method: str = Field("", description="Analysis method used")
    primary_language: str = Field("", description="Primary language")
    difficulty_range: list[str] = Field(default_factory=list, description="CEFR difficulty range")
    modules: list[ModuleMetadataSummary] = Field(default_factory=list, description="List of module summaries")


# =============================================================================
# Vocabulary Schemas
# =============================================================================


class VocabularyWordAudio(BaseModel):
    """Audio file references for a vocabulary word."""

    word: str | None = Field(None, description="Path to word audio file")
    translation: str | None = Field(None, description="Path to translation audio file")


class VocabularyWordResponse(BaseModel):
    """Response schema for a single vocabulary word."""

    id: str = Field(..., description="Word identifier")
    word: str = Field(..., description="The vocabulary word")
    translation: str = Field("", description="Translation of the word")
    definition: str = Field("", description="Definition of the word")
    part_of_speech: str = Field("", description="Part of speech")
    level: str = Field("", description="CEFR level")
    example: str = Field("", description="Example sentence")
    module_id: int | None = Field(None, description="Module where word appears")
    module_title: str | None = Field(None, description="Module title where word appears")
    page: int | None = Field(None, description="Page where word appears")
    audio: VocabularyWordAudio | None = Field(None, description="Audio file references")


class VocabularyResponse(BaseModel):
    """Response schema for vocabulary data."""

    book_id: str | int = Field(..., description="Book identifier")
    language: str = Field("", description="Primary language")
    translation_language: str = Field("", description="Translation language")
    total_words: int = Field(0, description="Total number of vocabulary words")
    words: list[VocabularyWordResponse] = Field(default_factory=list, description="List of vocabulary words")
    extracted_at: str | None = Field(None, description="When vocabulary was extracted")


# =============================================================================
# Processing Metadata Schemas
# =============================================================================


class StageResultResponse(BaseModel):
    """Response schema for a processing stage result."""

    status: str = Field(..., description="Stage status (pending, completed, failed)")
    completed_at: str | None = Field(None, description="When stage completed")
    error_message: str | None = Field(None, description="Error message if failed")


class ProcessingMetadataResponse(BaseModel):
    """Response schema for processing metadata."""

    book_id: str | int = Field(..., description="Book identifier")
    processing_status: str = Field(..., description="Overall processing status")
    processing_started_at: str | None = Field(None, description="When processing started")
    processing_completed_at: str | None = Field(None, description="When processing completed")
    total_pages: int = Field(0, description="Total pages processed")
    total_modules: int = Field(0, description="Total modules created")
    total_vocabulary: int = Field(0, description="Total vocabulary words extracted")
    total_audio_files: int = Field(0, description="Total audio files generated")
    languages: list[str] = Field(default_factory=list, description="Languages in book")
    primary_language: str = Field("", description="Primary language")
    difficulty_range: list[str] = Field(default_factory=list, description="CEFR difficulty range")
    stages: dict[str, StageResultResponse] = Field(default_factory=dict, description="Processing stage results")
    errors: list[dict] = Field(default_factory=list, description="Processing errors")


# =============================================================================
# Audio URL Schema
# =============================================================================


class AudioUrlResponse(BaseModel):
    """Response schema for audio URL."""

    word: str = Field(..., description="The vocabulary word")
    language: str = Field(..., description="Language code")
    url: str = Field(..., description="Presigned URL for audio file")
    expires_in: int = Field(..., description="URL expiration time in seconds")
