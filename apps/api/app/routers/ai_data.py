"""API endpoints for retrieving AI-processed book data."""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decode_access_token, verify_api_key_from_db
from app.db import get_db
from app.repositories.book import BookRepository
from app.repositories.publisher import PublisherRepository
from app.repositories.user import UserRepository
from app.schemas.ai_data import (
    ModuleDetailResponse,
    ModuleListResponse,
    ModuleMetadataSummary,
    ModulesMetadataResponse,
    ModuleSummary,
    ProcessingMetadataResponse,
    StageResultResponse,
    VocabularyResponse,
    VocabularyWordAudio,
    VocabularyWordResponse,
)
from app.services.ai_data import get_ai_data_retrieval_service

router = APIRouter(prefix="/books", tags=["AI Data"])
_bearer_scheme = HTTPBearer(auto_error=True)
_book_repository = BookRepository()
_publisher_repository = PublisherRepository()
_user_repository = UserRepository()
logger = logging.getLogger(__name__)

# Supported language codes for audio
SUPPORTED_LANGUAGES = {"en", "tr", "de", "fr", "es", "it", "pt", "ru", "ar", "zh", "ja", "ko"}

# Cache durations in seconds
CACHE_METADATA = 60  # 1 minute for metadata (may change during processing)
CACHE_MODULES = 300  # 5 minutes for modules (relatively static)
CACHE_VOCABULARY = 300  # 5 minutes for vocabulary
CACHE_AUDIO = 3600  # 1 hour for audio URLs


def _require_auth(credentials: HTTPAuthorizationCredentials, db: Session) -> int:
    """Validate JWT token or API key and return user ID or -1 for API key auth."""
    token = credentials.credentials

    # Try JWT first
    try:
        payload = decode_access_token(token, settings=get_settings())
        subject = payload.get("sub")
        if subject is not None:
            try:
                user_id = int(subject)
                user = _user_repository.get(db, user_id)
                if user is not None:
                    return user_id
            except (TypeError, ValueError):
                pass
    except ValueError:
        pass  # JWT failed, try API key

    # Try API key
    api_key_info = verify_api_key_from_db(token, db)
    if api_key_info is not None:
        return -1  # API key authentication

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
    )


def _get_book_info(db: Session, book_id: int) -> tuple[str, str]:
    """Get publisher ID (as string) and book name for a book ID.

    Returns:
        Tuple of (publisher_id_str, book_name)

    Raises:
        HTTPException 404 if book not found
    """
    book = _book_repository.get_by_id(db, book_id)
    if book is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Book not found",
        )
    # Use publisher ID for storage path construction
    publisher_id_str = str(book.publisher_id)
    return publisher_id_str, book.book_name


# =============================================================================
# Metadata Endpoint
# =============================================================================


@router.get(
    "/{book_id}/ai-data/metadata",
    response_model=ProcessingMetadataResponse,
)
def get_ai_metadata(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get AI processing metadata for a book.

    Returns metadata about the AI processing status, including:
    - Processing status and timestamps
    - Total pages, modules, vocabulary, and audio files
    - Language and difficulty information
    - Stage completion status

    Args:
        book_id: ID of the book

    Returns:
        ProcessingMetadataResponse with metadata

    Raises:
        401: Invalid authentication
        404: Book not found or not processed
    """
    _require_auth(credentials, db)
    publisher, book_name = _get_book_info(db, book_id)

    retrieval_service = get_ai_data_retrieval_service()
    metadata = retrieval_service.get_metadata(publisher, str(book_id), book_name)

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI data not found for this book",
        )

    # Convert stages to response format
    # Legacy stages replaced by chunked_analysis/unified_analysis
    legacy_stages = {"segmentation", "topic_analysis", "vocabulary"}
    uses_unified_analysis = (
        "chunked_analysis" in metadata.stages and metadata.stages["chunked_analysis"].status.value == "completed"
    ) or ("unified_analysis" in metadata.stages and metadata.stages["unified_analysis"].status.value == "completed")

    stages_response = {}
    for stage_name, stage_result in metadata.stages.items():
        # Hide legacy stages when unified/chunked analysis is used
        if uses_unified_analysis and stage_name in legacy_stages:
            continue
        stages_response[stage_name] = StageResultResponse(
            status=stage_result.status.value,
            completed_at=stage_result.completed_at.isoformat() if stage_result.completed_at else None,
            error_message=stage_result.error_message if stage_result.error_message else None,
        )

    response_data = ProcessingMetadataResponse(
        book_id=metadata.book_id,
        processing_status=metadata.processing_status.value,
        processing_started_at=metadata.processing_started_at.isoformat() if metadata.processing_started_at else None,
        processing_completed_at=metadata.processing_completed_at.isoformat()
        if metadata.processing_completed_at
        else None,
        total_pages=metadata.total_pages,
        total_modules=metadata.total_modules,
        total_vocabulary=metadata.total_vocabulary,
        total_audio_files=metadata.total_audio_files,
        languages=metadata.languages,
        primary_language=metadata.primary_language,
        difficulty_range=metadata.difficulty_range,
        stages=stages_response,
        errors=metadata.errors,
    )

    response = JSONResponse(content=response_data.model_dump())
    response.headers["Cache-Control"] = f"public, max-age={CACHE_METADATA}"
    return response


# =============================================================================
# Bulk AI Summary Endpoint
# =============================================================================


class _BulkAISummaryRequest(BaseModel):
    """Request body for bulk AI summary retrieval."""

    book_ids: list[int] = Field(..., max_length=100, description="List of book IDs (max 100)")


class _BookAISummary(BaseModel):
    """Summary of AI processing status for a single book."""

    book_id: int
    processing_status: str  # pending, processing, completed, failed, not_found
    total_modules: int = 0
    total_vocabulary: int = 0
    total_audio_files: int = 0
    primary_language: str | None = None


@router.post(
    "/ai-data/summary",
    response_model=list[_BookAISummary],
)
def get_bulk_ai_summary(
    payload: _BulkAISummaryRequest,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get AI processing summary for multiple books in a single request.

    Returns processing status and counts for each book.
    Books without AI data return status 'not_found'.
    """
    _require_auth(credentials, db)

    if not payload.book_ids:
        return JSONResponse(content=[])

    retrieval_service = get_ai_data_retrieval_service()
    results: list[dict] = []

    for book_id in payload.book_ids:
        # Look up book info
        book = _book_repository.get_by_id(db, book_id)
        if book is None:
            results.append({"book_id": book_id, "processing_status": "not_found"})
            continue

        publisher_id_str = str(book.publisher_id)

        metadata = retrieval_service.get_metadata(publisher_id_str, str(book_id), book.book_name)
        if metadata is None:
            results.append({"book_id": book_id, "processing_status": "not_found"})
            continue

        results.append(
            _BookAISummary(
                book_id=book_id,
                processing_status=metadata.processing_status.value,
                total_modules=metadata.total_modules,
                total_vocabulary=metadata.total_vocabulary,
                total_audio_files=metadata.total_audio_files,
                primary_language=metadata.primary_language,
            ).model_dump()
        )

    response = JSONResponse(content=results)
    response.headers["Cache-Control"] = f"public, max-age={CACHE_METADATA}"
    return response


# =============================================================================
# Modules Endpoints
# =============================================================================


@router.get(
    "/{book_id}/ai-data/modules",
    response_model=ModuleListResponse,
)
def list_ai_modules(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """List all modules for a book.

    Returns a list of module summaries with module_id, title, pages, and word_count.

    Args:
        book_id: ID of the book

    Returns:
        ModuleListResponse with list of modules

    Raises:
        401: Invalid authentication
        404: Book not found or no modules found
    """
    _require_auth(credentials, db)
    publisher, book_name = _get_book_info(db, book_id)

    retrieval_service = get_ai_data_retrieval_service()
    modules = retrieval_service.list_modules(publisher, str(book_id), book_name)

    if modules is None or len(modules) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No modules found for this book",
        )

    # Build module summaries
    module_summaries = [
        ModuleSummary(
            module_id=m.get("module_id", 0),
            title=m.get("title", ""),
            pages=m.get("pages", []),
            word_count=m.get("word_count", 0),
        )
        for m in modules
    ]

    response_data = ModuleListResponse(
        book_id=str(book_id),
        total_modules=len(module_summaries),
        modules=module_summaries,
    )

    response = JSONResponse(content=response_data.model_dump())
    response.headers["Cache-Control"] = f"public, max-age={CACHE_MODULES}"
    return response


@router.get(
    "/{book_id}/ai-data/modules/metadata",
    response_model=ModulesMetadataResponse,
)
def get_ai_modules_metadata(
    book_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get modules metadata.json with summary info for all modules.

    Returns analysis metadata including book info, processing method,
    and detailed summaries for each module (page ranges, topics, vocabulary counts).

    Args:
        book_id: ID of the book

    Returns:
        ModulesMetadataResponse with full metadata

    Raises:
        401: Invalid authentication
        404: Book not found or metadata not found
    """
    _require_auth(credentials, db)
    publisher, book_name = _get_book_info(db, book_id)

    retrieval_service = get_ai_data_retrieval_service()
    metadata = retrieval_service.get_modules_metadata(publisher, str(book_id), book_name)

    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Modules metadata not found for this book",
        )

    # Build module summaries
    module_summaries = [
        ModuleMetadataSummary(
            module_id=m.get("module_id", 0),
            title=m.get("title", ""),
            start_page=m.get("start_page", 0),
            end_page=m.get("end_page", 0),
            page_count=m.get("page_count", 0),
            word_count=m.get("word_count", 0),
            topics=m.get("topics", []),
            difficulty_level=m.get("difficulty_level", ""),
            summary=m.get("summary", ""),
            vocabulary_count=m.get("vocabulary_count", 0),
        )
        for m in metadata.get("modules", [])
    ]

    response_data = ModulesMetadataResponse(
        book_id=metadata.get("book_id", str(book_id)),
        publisher_id=metadata.get("publisher_id", ""),
        book_name=metadata.get("book_name", ""),
        total_pages=metadata.get("total_pages", 0),
        module_count=metadata.get("module_count", 0),
        method=metadata.get("method", ""),
        primary_language=metadata.get("primary_language", ""),
        difficulty_range=metadata.get("difficulty_range", []),
        modules=module_summaries,
    )

    response = JSONResponse(content=response_data.model_dump())
    response.headers["Cache-Control"] = f"public, max-age={CACHE_MODULES}"
    return response


@router.get(
    "/{book_id}/ai-data/modules/{module_id}",
    response_model=ModuleDetailResponse,
)
def get_ai_module(
    book_id: int,
    module_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get full data for a single module.

    Returns complete module data including text content, topics, and vocabulary IDs.

    Args:
        book_id: ID of the book
        module_id: ID of the module

    Returns:
        ModuleDetailResponse with full module data

    Raises:
        401: Invalid authentication
        404: Book not found or module not found
    """
    _require_auth(credentials, db)
    publisher, book_name = _get_book_info(db, book_id)

    retrieval_service = get_ai_data_retrieval_service()
    module = retrieval_service.get_module(publisher, str(book_id), book_name, module_id)

    if module is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Module {module_id} not found",
        )

    # Map stored fields to API response fields
    # Storage uses: difficulty_level, vocabulary (inline array)
    # API expects: difficulty, vocabulary_ids
    vocabulary_data = module.get("vocabulary", [])
    vocabulary_ids = (
        [v.get("word", "") for v in vocabulary_data] if vocabulary_data else module.get("vocabulary_ids", [])
    )

    response_data = ModuleDetailResponse(
        module_id=module.get("module_id", 0),
        title=module.get("title", ""),
        pages=module.get("pages", []),
        text=module.get("text", ""),
        topics=module.get("topics", []),
        grammar_points=module.get("grammar_points", []),
        vocabulary_ids=vocabulary_ids,
        language=module.get("language", ""),
        summary=module.get("summary", ""),
        difficulty=module.get("difficulty_level", module.get("difficulty", "")),
        word_count=module.get("word_count", 0),
        extracted_at=module.get("extracted_at"),
    )

    response = JSONResponse(content=response_data.model_dump())
    response.headers["Cache-Control"] = f"public, max-age={CACHE_MODULES}"
    return response


# =============================================================================
# Vocabulary Endpoint
# =============================================================================


@router.get(
    "/{book_id}/ai-data/vocabulary",
    response_model=VocabularyResponse,
)
def get_ai_vocabulary(
    book_id: int,
    module: int | None = Query(None, description="Filter vocabulary by module ID"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Get vocabulary data for a book.

    Returns vocabulary words with translations, definitions, and audio references.
    Optionally filter by module ID.

    Args:
        book_id: ID of the book
        module: Optional module ID to filter by

    Returns:
        VocabularyResponse with vocabulary words

    Raises:
        401: Invalid authentication
        404: Book not found or vocabulary not found
    """
    _require_auth(credentials, db)
    publisher, book_name = _get_book_info(db, book_id)

    retrieval_service = get_ai_data_retrieval_service()
    vocabulary = retrieval_service.get_vocabulary(publisher, str(book_id), book_name, module_id=module)

    if vocabulary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vocabulary not found for this book",
        )

    # Build vocabulary word responses
    words = []
    for word_data in vocabulary.get("words", []):
        audio_data = word_data.get("audio")
        audio = None
        if audio_data:
            audio = VocabularyWordAudio(
                word=audio_data.get("word"),
                translation=audio_data.get("translation"),
            )

        words.append(
            VocabularyWordResponse(
                id=word_data.get("id", ""),
                word=word_data.get("word", ""),
                translation=word_data.get("translation", ""),
                definition=word_data.get("definition", ""),
                part_of_speech=word_data.get("part_of_speech", ""),
                level=word_data.get("level", ""),
                example=word_data.get("example", ""),
                module_id=word_data.get("module_id"),
                module_title=word_data.get("module_title"),
                page=word_data.get("page"),
                audio=audio,
            )
        )

    response_data = VocabularyResponse(
        book_id=str(book_id),
        language=vocabulary.get("language", ""),
        translation_language=vocabulary.get("translation_language", ""),
        total_words=vocabulary.get("total_words", len(words)),
        words=words,
        extracted_at=vocabulary.get("extracted_at"),
    )

    response = JSONResponse(content=response_data.model_dump())
    response.headers["Cache-Control"] = f"public, max-age={CACHE_VOCABULARY}"
    return response


# =============================================================================
# Audio URL Endpoint
# =============================================================================


@router.get(
    "/{book_id}/ai-data/audio/vocabulary/{lang}/{word_id}.mp3",
)
def stream_vocabulary_audio(
    book_id: int,
    lang: str,
    word_id: str,
    range_header: str | None = Header(None, alias="Range"),
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    db: Session = Depends(get_db),
):
    """Stream vocabulary audio file directly.

    Streams the audio pronunciation of a vocabulary word.
    Supports HTTP Range requests for seeking.

    Args:
        book_id: ID of the book
        lang: Language code (e.g., 'en', 'tr')
        word_id: The vocabulary word ID (e.g., 'word_1', 'word_2')

    Returns:
        Audio file stream (audio/mpeg)

    Raises:
        400: Invalid language code or word_id format
        401: Invalid authentication
        404: Book not found or audio file not found
    """
    from minio.error import S3Error

    from app.services.minio import get_minio_client

    _require_auth(credentials, db)

    # Validate language code
    if lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported language code: {lang}. Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}",
        )

    # Basic word_id validation - alphanumeric, hyphens, underscores
    if not re.match(r"^[\w\-]+$", word_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid word_id format",
        )

    publisher_id_str, book_name = _get_book_info(db, book_id)
    settings = get_settings()
    client = get_minio_client(settings)

    # Build audio file path
    audio_path = f"{publisher_id_str}/books/{book_name}/ai-data/audio/vocabulary/{lang}/{word_id}.mp3"

    # Get file metadata
    try:
        stat = client.stat_object(settings.minio_publishers_bucket, audio_path)
    except S3Error as e:
        if e.code == "NoSuchKey":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Audio file not found for word_id '{word_id}' in language '{lang}'",
            )
        raise

    file_size = stat.size
    start = 0
    end = file_size - 1

    # Parse Range header if present
    if range_header:
        range_match = re.match(r"bytes=(\d*)-(\d*)", range_header)
        if range_match:
            start_str, end_str = range_match.groups()
            if start_str:
                start = int(start_str)
            if end_str:
                end = int(end_str)
            else:
                end = file_size - 1

            if start >= file_size or end >= file_size or start > end:
                raise HTTPException(
                    status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
                    detail="Range not satisfiable",
                )

    content_length = end - start + 1

    # Stream the file
    def iter_file():
        response = client.get_object(
            settings.minio_publishers_bucket,
            audio_path,
            offset=start,
            length=content_length,
        )
        try:
            for chunk in response.stream(8192):
                yield chunk
        finally:
            response.close()
            response.release_conn()

    headers = {
        "Content-Length": str(content_length),
        "Accept-Ranges": "bytes",
        "Cache-Control": f"public, max-age={CACHE_AUDIO}",
    }

    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        return StreamingResponse(
            iter_file(),
            status_code=status.HTTP_206_PARTIAL_CONTENT,
            media_type="audio/mpeg",
            headers=headers,
        )

    return StreamingResponse(
        iter_file(),
        media_type="audio/mpeg",
        headers=headers,
    )
