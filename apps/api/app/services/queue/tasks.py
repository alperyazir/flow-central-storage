"""Worker task definitions for arq."""

import fcntl
import logging
import shutil
from typing import Any

from app.core.config import get_settings
from app.services.ai_data import (
    ProcessingStatus as AIDataProcessingStatus,
)

# Import AI data services for metadata and structure management
from app.services.ai_data import (
    get_ai_data_cleanup_manager,
    get_ai_data_metadata_service,
    get_ai_data_structure_manager,
)

# Import audio generation service for audio_generation stage
from app.services.audio_generation import get_audio_generation_service, get_audio_storage

# Import PDF extraction service for text_extraction stage
from app.services.pdf import get_ai_storage, get_extraction_service
from app.services.queue.models import (
    ANALYSIS_ONLY_STAGES,
    FULL_PROCESSING_STAGES,
    LLM_ONLY_STAGES,
    MATERIAL_FULL_STAGES,
    MATERIAL_LLM_ONLY_STAGES,
    MATERIAL_TEXT_ONLY_STAGES,
    UNIFIED_PROCESSING_STAGES,
    ProcessingJobType,
    ProcessingStatus,
    QueueError,
)
from app.services.queue.redis import get_redis_connection
from app.services.queue.repository import JobRepository
from app.services.queue.service import ProgressReporter

# Import segmentation service for segmentation stage
from app.services.segmentation import get_module_storage, get_segmentation_service

# Import topic analysis service for topic_analysis stage
from app.services.topic_analysis import get_topic_analysis_service, get_topic_storage

# Import vocabulary extraction service for vocabulary stage
from app.services.vocabulary_extraction import get_vocabulary_extraction_service, get_vocabulary_storage

logger = logging.getLogger(__name__)


async def process_book_task(
    ctx: dict[str, Any],
    job_id: str,
    book_id: str,
    publisher_id: str,
    job_type: str,
    book_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Main entry point for book processing.

    This task orchestrates the full book processing pipeline including:
    - Text extraction from PDF
    - Content segmentation
    - AI topic analysis
    - Vocabulary extraction
    - Audio generation

    Args:
        ctx: arq context with Redis connection
        job_id: Processing job ID
        book_id: Book to process
        publisher_id: Publisher owning the book
        job_type: Type of processing (full, text_only, etc.)
        book_name: Book folder name (required for storage path)
        metadata: Additional job metadata

    Returns:
        Result dict with status and any output data

    Raises:
        QueueError: If processing fails after all retries
    """
    settings = get_settings()
    redis_conn = await get_redis_connection(url=settings.redis_url)
    repository = JobRepository(
        redis_client=redis_conn.client,
        job_ttl_seconds=settings.queue_job_ttl_seconds,
    )
    progress = ProgressReporter(repository, job_id)

    # Merge metadata with book_name - extract book_name from metadata if not provided directly
    job_metadata = metadata or {}
    if book_name:
        job_metadata["book_name"] = book_name
    elif "book_name" in job_metadata:
        book_name = job_metadata["book_name"]

    # Use publisher_id (int) directly for storage paths
    # The publisher_id parameter is the actual numeric publisher ID
    pub_id = int(publisher_id) if isinstance(publisher_id, str) and publisher_id.isdigit() else publisher_id

    # Store pub_id in metadata so stage functions can access it
    job_metadata["publisher_id_int"] = pub_id

    logger.info(
        "Starting processing job %s for book %s (type: %s, publisher_id: %s)",
        job_id,
        book_id,
        job_type,
        pub_id,
    )

    # Update job status to processing
    await repository.update_job_status(job_id, ProcessingStatus.PROCESSING)

    job_type_enum = ProcessingJobType(job_type)
    stages_to_run = _get_stages_for_job_type(job_type_enum)
    errors: list[dict] = []
    completed_stages: list[str] = []
    stage_results: dict[str, Any] = {}  # Store results for passing between stages

    # Get AI data services
    metadata_service = get_ai_data_metadata_service()
    structure_manager = get_ai_data_structure_manager()
    cleanup_manager = get_ai_data_cleanup_manager()

    try:
        # Initialize AI data structure and metadata using publisher_id (int)
        if book_name:
            # Check if reprocessing - cleanup existing ai-data if metadata exists
            if metadata_service.metadata_exists(pub_id, book_id, book_name):
                logger.info("Reprocessing detected, cleaning up existing ai-data")
                cleanup_manager.cleanup_all(pub_id, book_id, book_name)

            # Initialize directory structure
            structure_manager.initialize_ai_data_structure(pub_id, book_id, book_name)

            # Create initial metadata.json
            metadata_service.create_metadata(
                book_id=book_id,
                publisher_id=pub_id,
                book_name=book_name,
            )
            logger.info("Initialized ai-data structure and metadata for book %s", book_id)

        for stage in stages_to_run:
            try:
                result = await _run_processing_stage(
                    stage=stage,
                    job_id=job_id,
                    book_id=book_id,
                    publisher_id=publisher_id,
                    progress=progress,
                    metadata=job_metadata,
                    stage_results=stage_results,
                )
                if result:
                    stage_results[stage] = result
                completed_stages.append(stage)
                await progress.report_step_complete(stage)

                # Update metadata.json with stage results
                if book_name and result:
                    metadata_service.update_metadata(
                        publisher_id=pub_id,
                        book_id=book_id,
                        book_name=book_name,
                        stage_name=stage,
                        stage_result=result,
                        success=True,
                    )

            except Exception as stage_error:
                logger.error(
                    "Stage %s failed for job %s: %s",
                    stage,
                    job_id,
                    stage_error,
                )
                errors.append(
                    {
                        "stage": stage,
                        "error": str(stage_error),
                    }
                )

                # Update metadata.json with stage failure
                if book_name:
                    metadata_service.update_metadata(
                        publisher_id=pub_id,
                        book_id=book_id,
                        book_name=book_name,
                        stage_name=stage,
                        stage_result={},
                        success=False,
                        error_message=str(stage_error),
                    )

                # Continue on non-critical errors for partial completion
                if not _is_critical_stage(stage):
                    logger.warning(
                        "Continuing after non-critical stage failure: %s",
                        stage,
                    )
                    continue

                # Critical stage failed - abort
                raise

        # All stages completed
        if errors:
            # Some non-critical stages failed
            await repository.update_job_status(
                job_id,
                ProcessingStatus.PARTIAL,
                error_message=f"Partial completion: {len(errors)} stage(s) failed",
            )

            # Finalize metadata with partial status
            if book_name:
                metadata_service.finalize_metadata(
                    publisher_id=pub_id,
                    book_id=book_id,
                    book_name=book_name,
                    final_status=AIDataProcessingStatus.PARTIAL,
                    error_message=f"{len(errors)} stage(s) failed",
                )

            logger.warning(
                "Job %s completed partially with %d error(s)",
                job_id,
                len(errors),
            )
            return {
                "status": "partial",
                "completed_stages": completed_stages,
                "errors": errors,
            }

        await repository.update_job_status(job_id, ProcessingStatus.COMPLETED)

        # Finalize metadata with completed status
        if book_name:
            metadata_service.finalize_metadata(
                publisher_id=pub_id,
                book_id=book_id,
                book_name=book_name,
                final_status=AIDataProcessingStatus.COMPLETED,
            )

        logger.info("Job %s completed successfully", job_id)
        return {
            "status": "completed",
            "completed_stages": completed_stages,
        }

    except Exception as e:
        logger.error("Job %s failed: %s", job_id, e)
        await repository.update_job_status(
            job_id,
            ProcessingStatus.FAILED,
            error_message=str(e),
        )

        # Finalize metadata with failed status
        if book_name:
            try:
                metadata_service.finalize_metadata(
                    publisher_id=pub_id,
                    book_id=book_id,
                    book_name=book_name,
                    final_status=AIDataProcessingStatus.FAILED,
                    error_message=str(e),
                )
            except Exception as meta_err:
                logger.warning("Failed to finalize metadata on error: %s", meta_err)

        raise QueueError(f"Processing failed: {e}") from e


def _get_stages_for_job_type(job_type: ProcessingJobType) -> list[str]:
    """Get processing stages for a job type.

    Args:
        job_type: Type of processing job

    Returns:
        List of stage names to execute
    """
    # Main 4 options (new chunked approach)
    if job_type == ProcessingJobType.FULL:
        # Full pipeline with chunked LLM analysis
        return list(FULL_PROCESSING_STAGES.keys())
    elif job_type == ProcessingJobType.TEXT_ONLY:
        return ["text_extraction"]
    elif job_type == ProcessingJobType.LLM_ONLY:
        # Chunked LLM analysis only
        return list(LLM_ONLY_STAGES.keys())
    elif job_type == ProcessingJobType.AUDIO_ONLY:
        return ["audio_generation"]

    # Legacy options (backwards compatibility)
    elif job_type == ProcessingJobType.UNIFIED:
        # Legacy: single LLM call approach
        return list(UNIFIED_PROCESSING_STAGES.keys())
    elif job_type == ProcessingJobType.ANALYSIS_ONLY:
        # Legacy: text + unified analysis (no audio)
        return list(ANALYSIS_ONLY_STAGES.keys())
    elif job_type == ProcessingJobType.VOCABULARY_ONLY:
        return ["vocabulary"]

    # Fallback to full processing
    return list(FULL_PROCESSING_STAGES.keys())


def _is_critical_stage(stage: str) -> bool:
    """Check if a stage failure should abort processing.

    Args:
        stage: Stage name

    Returns:
        True if stage is critical
    """
    # Text extraction, segmentation, and analysis stages are critical
    critical_stages = {
        "text_extraction",
        "segmentation",
        "unified_analysis",
        "chunked_analysis",
    }
    return stage in critical_stages


async def _run_processing_stage(
    stage: str,
    job_id: str,
    book_id: str,
    publisher_id: str,
    progress: ProgressReporter,
    metadata: dict[str, Any] | None = None,
    stage_results: dict[str, Any] | None = None,
) -> Any:
    """Run a single processing stage.

    Args:
        stage: Stage name
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID
        progress: Progress reporter
        metadata: Job metadata (contains book_name, etc.)
        stage_results: Results from previous stages

    Returns:
        Stage result data (if any)
    """
    logger.info("Running stage %s for job %s", stage, job_id)
    metadata = metadata or {}
    stage_results = stage_results or {}

    # Report initial progress for stage
    await progress.report_progress(stage, 0)

    # Get the integer publisher_id from metadata
    pub_id = metadata.get("publisher_id_int", publisher_id)

    if stage == "text_extraction":
        return await _run_text_extraction(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
        )

    if stage == "segmentation":
        return await _run_segmentation(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            text_extraction_result=stage_results.get("text_extraction"),
        )

    if stage == "unified_analysis":
        return await _run_unified_analysis(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            text_extraction_result=stage_results.get("text_extraction"),
        )

    if stage == "chunked_analysis":
        return await _run_chunked_analysis(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            text_extraction_result=stage_results.get("text_extraction"),
        )

    if stage == "topic_analysis":
        return await _run_topic_analysis(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            segmentation_result=stage_results.get("segmentation"),
        )

    if stage == "vocabulary":
        return await _run_vocabulary_extraction(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            topic_analysis_result=stage_results.get("topic_analysis"),
        )

    if stage == "audio_generation":
        return await _run_audio_generation(
            job_id=job_id,
            book_id=book_id,
            publisher_id=pub_id,
            book_name=metadata.get("book_name", ""),
            progress=progress,
            vocabulary_result=stage_results.get("vocabulary"),
        )

    await progress.report_progress(stage, 50)
    await progress.report_progress(stage, 100)

    logger.info("Completed stage %s for job %s", stage, job_id)
    return None


async def _run_text_extraction(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
) -> dict[str, Any]:
    """Run text extraction stage.

    Extracts text from the book's PDF and stores it in ai-data.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter

    Returns:
        Extraction result data

    Raises:
        QueueError: If book_name is missing or extraction fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for text extraction",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting text extraction for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Progress tracking for async update after extraction
    last_progress = {"current": 0, "total": 0}

    def on_progress(current: int, total: int) -> None:
        """Sync callback to track progress."""
        last_progress["current"] = current
        last_progress["total"] = total

    # Get extraction service and storage
    extraction_service = get_extraction_service()
    ai_storage = get_ai_storage()

    # Clean up any existing text files before re-extraction
    ai_storage.cleanup_text_directory(publisher_id, book_id, book_name)

    # Extract text from PDF
    result = await extraction_service.extract_book_pdf(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        progress_callback=on_progress,
    )

    # Report final progress
    await progress.report_progress("text_extraction", 100)

    # Save extracted text to storage
    saved = ai_storage.save_all(result)

    logger.info(
        "Text extraction completed: %d pages, %d words, method=%s",
        result.total_pages,
        result.total_word_count,
        result.method.value,
    )

    return {
        "total_pages": result.total_pages,
        "total_word_count": result.total_word_count,
        "method": result.method.value,
        "scanned_pages": result.scanned_page_count,
        "native_pages": result.native_page_count,
        "saved_files": len(saved.get("text_files", [])),
        "metadata_path": saved.get("metadata"),
    }


async def _run_segmentation(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    text_extraction_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run segmentation stage.

    Segments the extracted text into logical modules/chapters.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        text_extraction_result: Result from text extraction stage

    Returns:
        Segmentation result data

    Raises:
        QueueError: If book_name is missing or segmentation fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for segmentation",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting segmentation for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Progress tracking
    def on_progress(current: int, total: int) -> None:
        """Sync callback to track progress."""
        pass  # Progress is reported via ProgressReporter

    # Get segmentation service and storage
    segmentation_service = get_segmentation_service()
    module_storage = get_module_storage()

    # Clean up any existing module files before re-segmentation
    module_storage.cleanup_modules_directory(publisher_id, book_id, book_name)

    # Run segmentation
    result = await segmentation_service.segment_book(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        progress_callback=on_progress,
    )

    # Report progress at 80%
    await progress.report_progress("segmentation", 80)

    # Save modules to storage
    saved = module_storage.save_all(result)

    # Report final progress
    await progress.report_progress("segmentation", 100)

    logger.info(
        "Segmentation completed: %d modules, method=%s",
        result.module_count,
        result.method.value,
    )

    return {
        "module_count": result.module_count,
        "total_word_count": result.total_word_count,
        "method": result.method.value,
        "saved_modules": len(saved.get("modules", [])),
        "metadata_path": saved.get("metadata"),
        "modules": [{"id": m.module_id, "title": m.title, "pages": len(m.pages)} for m in result.modules],
    }


async def _run_unified_analysis(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    text_extraction_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run unified AI analysis stage.

    Combines segmentation, topic analysis, and vocabulary extraction
    into a single LLM call for better accuracy and lower cost.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        text_extraction_result: Result from text extraction stage

    Returns:
        Unified analysis result data

    Raises:
        QueueError: If book_name is missing or analysis fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for unified analysis",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting unified AI analysis for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Import unified analysis service
    from app.services.unified_analysis import (
        get_unified_analysis_service,
        get_unified_analysis_storage,
    )

    # Get services
    unified_service = get_unified_analysis_service()
    unified_storage = get_unified_analysis_storage()

    # Load extracted text pages
    pages = await _load_text_pages_for_analysis(publisher_id, book_id, book_name)

    if not pages:
        raise QueueError(
            "No extracted text found for unified analysis",
            {"job_id": job_id, "book_id": book_id},
        )

    # Progress callback
    def on_progress(current: int, total: int) -> None:
        pass  # Progress reported via ProgressReporter

    # Report initial progress
    await progress.report_progress("unified_analysis", 10)

    # Run unified analysis
    result = await unified_service.analyze_book(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        pages=pages,
        progress_callback=on_progress,
    )

    # Report progress at 70%
    await progress.report_progress("unified_analysis", 70)

    # Save results
    saved = unified_storage.save_all(result)

    # Report final progress
    await progress.report_progress("unified_analysis", 100)

    logger.info(
        "Unified analysis completed: %d modules, %d vocabulary words, %.2fs",
        result.module_count,
        result.total_vocabulary,
        result.processing_time_seconds,
    )

    return {
        "module_count": result.module_count,
        "total_vocabulary": result.total_vocabulary,
        "total_topics": result.total_topics,
        "primary_language": result.primary_language,
        "translation_language": result.translation_language,
        "difficulty_range": result.difficulty_range,
        "method": result.method,
        "processing_time_seconds": result.processing_time_seconds,
        "saved_modules": saved.get("module_count", 0),
        "saved_vocabulary": saved.get("vocabulary_count", 0),
    }


async def _run_chunked_analysis(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    text_extraction_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run chunked AI analysis stage.

    Uses two-phase approach:
    - Phase 1: Detect all modules (structure only)
    - Phase 2: Extract vocabulary per module (with retries)

    This approach is more reliable for large books.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        text_extraction_result: Result from text extraction stage

    Returns:
        Chunked analysis result data

    Raises:
        QueueError: If book_name is missing or analysis fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for chunked analysis",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting chunked AI analysis for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Import chunked analysis service
    from app.services.unified_analysis import (
        get_unified_analysis_service,
        get_unified_analysis_storage,
    )
    from app.services.unified_analysis.models import ChunkedProgress

    # Get services
    unified_service = get_unified_analysis_service()
    unified_storage = get_unified_analysis_storage()

    # Load extracted text pages
    pages = await _load_text_pages_for_analysis(publisher_id, book_id, book_name)

    if not pages:
        raise QueueError(
            "No extracted text found for chunked analysis",
            {"job_id": job_id, "book_id": book_id},
        )

    # Progress callbacks - sync wrappers for async progress reporting
    import asyncio

    def sync_detailed_progress(chunked_progress: ChunkedProgress) -> None:
        """Sync callback that schedules async progress update."""
        if chunked_progress.phase == "detecting_modules":
            step_detail = "Detecting modules..."
        elif chunked_progress.phase == "extracting_vocabulary":
            step_detail = f"Module {chunked_progress.current_module}/{chunked_progress.total_modules}: {chunked_progress.module_title}"
            if chunked_progress.retry_count > 0:
                step_detail += f" (retry {chunked_progress.retry_count})"
        elif chunked_progress.phase == "complete":
            step_detail = "Analysis complete"
        else:
            step_detail = "Processing..."

        # Schedule async progress report
        asyncio.create_task(progress.report_progress("chunked_analysis", chunked_progress.overall_percent, step_detail))

    # Report initial progress
    await progress.report_progress("chunked_analysis", 5)

    # Run chunked analysis
    result = await unified_service.analyze_book_chunked(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        pages=pages,
        progress_callback=None,
        detailed_progress_callback=sync_detailed_progress,
    )

    # Report progress at 90%
    await progress.report_progress("chunked_analysis", 90)

    # Save results
    saved = unified_storage.save_all(result)

    # Report final progress
    await progress.report_progress("chunked_analysis", 100)

    logger.info(
        "Chunked analysis completed: %d modules, %d vocabulary words, %.2fs",
        result.module_count,
        result.total_vocabulary,
        result.processing_time_seconds,
    )

    return {
        "module_count": result.module_count,
        "total_vocabulary": result.total_vocabulary,
        "total_topics": result.total_topics,
        "primary_language": result.primary_language,
        "translation_language": result.translation_language,
        "difficulty_range": result.difficulty_range,
        "method": result.method,
        "processing_time_seconds": result.processing_time_seconds,
        "saved_modules": saved.get("module_count", 0),
        "saved_vocabulary": saved.get("vocabulary_count", 0),
    }


async def _load_text_pages_for_analysis(
    publisher_id: int,
    book_id: str,
    book_name: str,
) -> dict[int, str]:
    """Load extracted text pages for unified analysis."""
    from minio.error import S3Error

    from app.services.minio import get_minio_client

    settings = get_settings()
    client = get_minio_client(settings)
    bucket = settings.minio_publishers_bucket

    pages: dict[int, str] = {}

    # Get extraction metadata to know page count
    meta_path = f"{publisher_id}/books/{book_name}/ai-data/text/extraction_metadata.json"
    try:
        response = client.get_object(bucket, meta_path)
        meta_data = response.read()
        response.close()
        response.release_conn()

        import json

        metadata = json.loads(meta_data.decode("utf-8"))
        total_pages = metadata.get("total_pages", 0)

        # Load each page
        for page_num in range(1, total_pages + 1):
            page_path = f"{publisher_id}/books/{book_name}/ai-data/text/page_{page_num:03d}.txt"
            try:
                resp = client.get_object(bucket, page_path)
                text = resp.read().decode("utf-8")
                resp.close()
                resp.release_conn()
                if text.strip():
                    pages[page_num] = text
            except S3Error:
                continue

    except S3Error as e:
        logger.warning("Failed to load text metadata: %s", e)

    return pages


async def _run_topic_analysis(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    segmentation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run topic analysis stage.

    Analyzes module content with LLM to extract topics, difficulty, and language.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        segmentation_result: Result from segmentation stage

    Returns:
        Topic analysis result data

    Raises:
        QueueError: If book_name is missing or analysis fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for topic analysis",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting topic analysis for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Get topic analysis service and storage
    topic_service = get_topic_analysis_service()
    topic_storage = get_topic_storage()

    # Load modules from storage
    modules = topic_storage.list_modules(publisher_id, book_id, book_name)

    if not modules:
        logger.warning(
            "No modules found for topic analysis: %s/%s/%s",
            publisher_id,
            book_id,
            book_name,
        )
        await progress.report_progress("topic_analysis", 100)
        return {
            "module_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "primary_language": "",
            "difficulty_range": [],
        }

    # Progress tracking
    analyzed_count = 0

    def on_progress(current: int, total: int) -> None:
        """Sync callback to track progress."""
        nonlocal analyzed_count
        analyzed_count = current

    # Run topic analysis
    result = await topic_service.analyze_book(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        modules=modules,
        progress_callback=on_progress,
    )

    # Report progress at 80%
    await progress.report_progress("topic_analysis", 80)

    # Save analysis results (update module JSONs)
    saved = topic_storage.save_all(result)

    # Report final progress
    await progress.report_progress("topic_analysis", 100)

    logger.info(
        "Topic analysis completed: %d/%d modules succeeded, language=%s, difficulty=%s",
        result.success_count,
        len(modules),
        result.primary_language,
        result.difficulty_range,
    )

    return {
        "module_count": len(modules),
        "success_count": result.success_count,
        "failure_count": result.failure_count,
        "primary_language": result.primary_language,
        "difficulty_range": result.difficulty_range,
        "total_topics": result.total_topics,
        "total_grammar_points": result.total_grammar_points,
        "updated_modules": len(saved.get("updated", [])),
        "metadata_path": saved.get("metadata"),
    }


async def _run_vocabulary_extraction(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    topic_analysis_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run vocabulary extraction stage.

    Extracts vocabulary words from module content using LLM.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        topic_analysis_result: Result from topic analysis stage

    Returns:
        Vocabulary extraction result data

    Raises:
        QueueError: If book_name is missing or extraction fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for vocabulary extraction",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting vocabulary extraction for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Get vocabulary extraction service and storage
    vocab_service = get_vocabulary_extraction_service()
    vocab_storage = get_vocabulary_storage()

    # Load modules from storage
    modules = vocab_storage.list_modules(publisher_id, book_id, book_name)

    if not modules:
        logger.warning(
            "No modules found for vocabulary extraction: %s/%s/%s",
            publisher_id,
            book_id,
            book_name,
        )
        await progress.report_progress("vocabulary", 100)
        return {
            "module_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_words": 0,
            "language": "",
        }

    # Determine language from topic analysis result or modules
    primary_language = "en"
    translation_language = "tr"
    if topic_analysis_result:
        primary_language = topic_analysis_result.get("primary_language", "en") or "en"

    # Progress tracking
    def on_progress(current: int, total: int) -> None:
        """Sync callback to track progress."""
        pass  # Progress reported via ProgressReporter

    # Run vocabulary extraction
    result = await vocab_service.extract_book_vocabulary(
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        modules=modules,
        language=primary_language,
        translation_language=translation_language,
        progress_callback=on_progress,
    )

    # Report progress at 80%
    await progress.report_progress("vocabulary", 80)

    # Save vocabulary and update module JSONs
    saved = vocab_storage.save_all(result)

    # Report final progress
    await progress.report_progress("vocabulary", 100)

    logger.info(
        "Vocabulary extraction completed: %d/%d modules succeeded, %d unique words",
        result.success_count,
        len(modules),
        result.total_words,
    )

    return {
        "module_count": len(modules),
        "success_count": result.success_count,
        "failure_count": result.failure_count,
        "total_words": result.total_words,
        "language": result.language,
        "translation_language": result.translation_language,
        "vocabulary_path": saved.get("vocabulary"),
        "updated_modules": len(saved.get("updated", [])),
        "metadata_path": saved.get("metadata"),
    }


async def _run_audio_generation(
    job_id: str,
    book_id: str,
    publisher_id: int,
    book_name: str,
    progress: ProgressReporter,
    vocabulary_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run audio generation stage.

    Generates audio pronunciations for vocabulary words using TTS service.

    Args:
        job_id: Job ID
        book_id: Book ID
        publisher_id: Publisher ID (integer)
        book_name: Book folder name
        progress: Progress reporter
        vocabulary_result: Result from vocabulary extraction stage

    Returns:
        Audio generation result data

    Raises:
        QueueError: If book_name is missing or generation fails
    """
    if not book_name:
        raise QueueError(
            "book_name is required for audio generation",
            {"job_id": job_id, "book_id": book_id},
        )

    logger.info(
        "Starting audio generation for book %s (publisher_id: %s, name: %s)",
        book_id,
        publisher_id,
        book_name,
    )

    # Get audio generation service and storage
    audio_service = get_audio_generation_service()
    audio_storage = get_audio_storage()

    # Load vocabulary from storage
    try:
        vocabulary_data = audio_storage.load_vocabulary(publisher_id, book_id, book_name)
    except Exception as e:
        logger.warning(
            "No vocabulary found for audio generation: %s/%s/%s - %s",
            publisher_id,
            book_id,
            book_name,
            e,
        )
        await progress.report_progress("audio_generation", 100)
        return {
            "total_words": 0,
            "generated_count": 0,
            "failed_count": 0,
            "language": "",
            "translation_language": "",
        }

    vocabulary_words = vocabulary_data.get("words", [])
    if not vocabulary_words:
        logger.info("No vocabulary words to generate audio for")
        await progress.report_progress("audio_generation", 100)
        return {
            "total_words": 0,
            "generated_count": 0,
            "failed_count": 0,
            "language": vocabulary_data.get("language", "en"),
            "translation_language": vocabulary_data.get("translation_language", "tr"),
        }

    # Get language settings
    primary_language = vocabulary_data.get("language", "en")
    translation_language = vocabulary_data.get("translation_language", "tr")

    # Progress tracking
    def on_progress(current: int, total: int) -> None:
        """Sync callback to track progress."""
        pass  # Progress reported via ProgressReporter

    # Report initial progress
    await progress.report_progress("audio_generation", 10)

    # Clean up existing audio before re-generation
    audio_storage.cleanup_audio_directory(publisher_id, book_id, book_name)

    # Generate audio for all vocabulary words
    result, audio_data = await audio_service.generate_vocabulary_audio(
        vocabulary=vocabulary_words,
        book_id=book_id,
        publisher_id=publisher_id,
        book_name=book_name,
        language=primary_language,
        translation_language=translation_language,
        progress_callback=on_progress,
    )

    # Report progress at 50% (generation complete)
    await progress.report_progress("audio_generation", 50)

    # Save audio files to storage
    save_result = audio_storage.save_all_audio(
        publisher_id=publisher_id,
        book_id=book_id,
        book_name=book_name,
        audio_files=result.audio_files,
        audio_data=audio_data,
    )

    # Report progress at 80% (files saved)
    await progress.report_progress("audio_generation", 80)

    # Update vocabulary.json with audio paths
    if result.audio_files:
        audio_storage.update_vocabulary_audio_paths(
            publisher_id=publisher_id,
            book_id=book_id,
            book_name=book_name,
            audio_files=result.audio_files,
        )

    # Report final progress
    await progress.report_progress("audio_generation", 100)

    logger.info(
        "Audio generation completed: %d/%d generated, %d failed, %d files saved",
        result.generated_count,
        len(vocabulary_words),
        result.failed_count,
        save_result.get("saved", 0),
    )

    return {
        "total_words": len(vocabulary_words),
        "generated_count": result.generated_count,
        "failed_count": result.failed_count,
        "audio_files_saved": save_result.get("saved", 0),
        "audio_files_failed": save_result.get("failed", 0),
        "language": primary_language,
        "translation_language": translation_language,
    }


async def on_job_start(ctx: dict[str, Any]) -> None:
    """Called when a job starts.

    Args:
        ctx: arq context
    """
    logger.info("Worker starting job")


async def on_job_end(ctx: dict[str, Any]) -> None:
    """Called when a job ends.

    Args:
        ctx: arq context
    """
    logger.info("Worker finished job")


# =============================================================================
# Material Processing Task
# =============================================================================


async def process_material_task(
    ctx: dict[str, Any],
    job_id: str,
    material_id: int,
    teacher_id: str,
    job_type: str,
    material_name: str,
    file_type: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a teacher material through the AI pipeline.

    This task orchestrates material processing including:
    - Text extraction from PDF/TXT/DOCX
    - AI analysis (modules, vocabulary)
    - Audio generation for vocabulary

    Args:
        ctx: arq context with Redis connection
        job_id: Processing job ID
        material_id: Database ID of the material
        teacher_id: Teacher ID (folder name)
        job_type: Type of processing (material_full, material_text_only, etc.)
        material_name: Material filename
        file_type: File extension (pdf, txt, docx)
        metadata: Additional job metadata

    Returns:
        Result dict with status and output data

    Raises:
        QueueError: If processing fails
    """

    settings = get_settings()
    redis_conn = await get_redis_connection(url=settings.redis_url)
    repository = JobRepository(
        redis_client=redis_conn.client,
        job_ttl_seconds=settings.queue_job_ttl_seconds,
    )
    progress = ProgressReporter(repository, job_id)

    job_metadata = metadata or {}

    logger.info(
        "Starting material processing job %s for %s (teacher: %s, type: %s)",
        job_id,
        material_name,
        teacher_id,
        job_type,
    )

    # Update job status to processing
    await repository.update_job_status(job_id, ProcessingStatus.PROCESSING)

    job_type_enum = ProcessingJobType(job_type)
    stages_to_run = _get_material_stages_for_job_type(job_type_enum)
    errors: list[dict] = []
    completed_stages: list[str] = []
    stage_results: dict[str, Any] = {}

    try:
        for stage in stages_to_run:
            try:
                result = await _run_material_processing_stage(
                    stage=stage,
                    job_id=job_id,
                    material_id=material_id,
                    teacher_id=teacher_id,
                    material_name=material_name,
                    file_type=file_type,
                    progress=progress,
                    metadata=job_metadata,
                    stage_results=stage_results,
                )
                if result:
                    stage_results[stage] = result
                completed_stages.append(stage)
                await progress.report_step_complete(stage)

            except Exception as stage_error:
                logger.error(
                    "Material stage %s failed for job %s: %s",
                    stage,
                    job_id,
                    stage_error,
                )
                errors.append(
                    {
                        "stage": stage,
                        "error": str(stage_error),
                    }
                )

                # Text extraction is critical
                if stage == "material_text_extraction":
                    raise

                # Continue on non-critical errors
                logger.warning(
                    "Continuing after non-critical material stage failure: %s",
                    stage,
                )
                continue

        # All stages completed
        if errors:
            await repository.update_job_status(
                job_id,
                ProcessingStatus.PARTIAL,
                error_message=f"Partial completion: {len(errors)} stage(s) failed",
            )
            logger.warning(
                "Material job %s completed partially with %d error(s)",
                job_id,
                len(errors),
            )
            return {
                "status": "partial",
                "completed_stages": completed_stages,
                "errors": errors,
            }

        await repository.update_job_status(job_id, ProcessingStatus.COMPLETED)
        logger.info("Material job %s completed successfully", job_id)
        return {
            "status": "completed",
            "completed_stages": completed_stages,
        }

    except Exception as e:
        logger.error("Material job %s failed: %s", job_id, e)
        await repository.update_job_status(
            job_id,
            ProcessingStatus.FAILED,
            error_message=str(e),
        )
        raise QueueError(f"Material processing failed: {e}") from e


def _get_material_stages_for_job_type(job_type: ProcessingJobType) -> list[str]:
    """Get processing stages for a material job type.

    Args:
        job_type: Type of processing job

    Returns:
        List of stage names to execute
    """
    if job_type == ProcessingJobType.MATERIAL_FULL:
        return list(MATERIAL_FULL_STAGES.keys())
    elif job_type == ProcessingJobType.MATERIAL_TEXT_ONLY:
        return list(MATERIAL_TEXT_ONLY_STAGES.keys())
    elif job_type == ProcessingJobType.MATERIAL_LLM_ONLY:
        return list(MATERIAL_LLM_ONLY_STAGES.keys())
    else:
        # Default to full processing
        return list(MATERIAL_FULL_STAGES.keys())


async def _run_material_processing_stage(
    stage: str,
    job_id: str,
    material_id: int,
    teacher_id: str,
    material_name: str,
    file_type: str,
    progress: ProgressReporter,
    metadata: dict[str, Any] | None = None,
    stage_results: dict[str, Any] | None = None,
) -> Any:
    """Run a single material processing stage.

    Args:
        stage: Stage name
        job_id: Job ID
        material_id: Material database ID
        teacher_id: Teacher ID
        material_name: Material filename
        file_type: File extension
        progress: Progress reporter
        metadata: Job metadata
        stage_results: Results from previous stages

    Returns:
        Stage result data
    """
    logger.info("Running material stage %s for job %s", stage, job_id)
    metadata = metadata or {}
    stage_results = stage_results or {}

    await progress.report_progress(stage, 0)

    if stage == "material_text_extraction":
        return await _run_material_text_extraction(
            job_id=job_id,
            material_id=material_id,
            teacher_id=teacher_id,
            material_name=material_name,
            file_type=file_type,
            progress=progress,
        )

    if stage == "material_analysis":
        return await _run_material_analysis(
            job_id=job_id,
            material_id=material_id,
            teacher_id=teacher_id,
            material_name=material_name,
            progress=progress,
            text_extraction_result=stage_results.get("material_text_extraction"),
        )

    if stage == "material_audio":
        return await _run_material_audio_generation(
            job_id=job_id,
            material_id=material_id,
            teacher_id=teacher_id,
            material_name=material_name,
            progress=progress,
            analysis_result=stage_results.get("material_analysis"),
        )

    await progress.report_progress(stage, 100)
    logger.info("Completed material stage %s for job %s", stage, job_id)
    return None


async def _run_material_text_extraction(
    job_id: str,
    material_id: int,
    teacher_id: str,
    material_name: str,
    file_type: str,
    progress: ProgressReporter,
) -> dict[str, Any]:
    """Run material text extraction stage.

    Args:
        job_id: Job ID
        material_id: Material database ID
        teacher_id: Teacher ID
        material_name: Material filename
        file_type: File extension
        progress: Progress reporter

    Returns:
        Extraction result data
    """
    from app.services.material_ai_data import get_material_ai_storage
    from app.services.material_extraction import get_material_extraction_service

    logger.info(
        "Starting material text extraction for %s (teacher: %s, type: %s)",
        material_name,
        teacher_id,
        file_type,
    )

    extraction_service = get_material_extraction_service()
    ai_storage = get_material_ai_storage()

    # Progress callback
    def on_progress(current: int, total: int) -> None:
        pass  # Progress reported via ProgressReporter

    # Extract text from material
    result = await extraction_service.extract_material_text(
        material_id=material_id,
        teacher_id=teacher_id,
        material_name=material_name,
        file_type=file_type,
        progress_callback=on_progress,
    )

    # Report progress at 80%
    await progress.report_progress("material_text_extraction", 80)

    # Save extracted text to storage
    saved = ai_storage.save_extracted_text(result)

    # Report final progress
    await progress.report_progress("material_text_extraction", 100)

    logger.info(
        "Material text extraction completed: %d pages, %d words, method=%s",
        result.total_pages,
        result.total_word_count,
        result.method.value,
    )

    return {
        "total_pages": result.total_pages,
        "total_word_count": result.total_word_count,
        "method": result.method.value,
        "saved_files": len(saved.get("text_files", [])),
    }


async def _run_material_analysis(
    job_id: str,
    material_id: int,
    teacher_id: str,
    material_name: str,
    progress: ProgressReporter,
    text_extraction_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run material AI analysis stage.

    Args:
        job_id: Job ID
        material_id: Material database ID
        teacher_id: Teacher ID
        material_name: Material filename
        progress: Progress reporter
        text_extraction_result: Result from text extraction

    Returns:
        Analysis result data
    """
    from app.services.material_ai_data import get_material_ai_storage
    from app.services.unified_analysis import get_unified_analysis_service

    logger.info(
        "Starting material AI analysis for %s (teacher: %s)",
        material_name,
        teacher_id,
    )

    ai_storage = get_material_ai_storage()
    analysis_service = get_unified_analysis_service()

    # Load extracted text
    pages = ai_storage.load_extracted_text(teacher_id, material_name)

    if not pages:
        logger.warning("No extracted text found for material %s", material_name)
        await progress.report_progress("material_analysis", 100)
        return {
            "module_count": 0,
            "total_vocabulary": 0,
            "error": "No text found to analyze",
        }

    # Report initial progress
    await progress.report_progress("material_analysis", 10)

    # Run chunked analysis (reuse book analysis logic)
    # Use material_name as book_name, teacher_id as publisher
    result = await analysis_service.analyze_book_chunked(
        book_id=str(material_id),
        publisher_id=teacher_id,
        book_name=material_name,
        pages=pages,
        progress_callback=None,
    )

    # Report progress at 80%
    await progress.report_progress("material_analysis", 80)

    # Save analysis results
    modules = []
    vocabulary = []

    for module in result.modules:
        modules.append(
            {
                "id": module.module_id,
                "title": module.title,
                "pages": list(module.pages),
                "topics": module.topics,
                "grammar_points": module.grammar_points,
                "difficulty": module.difficulty,
                "vocabulary": [w.model_dump() for w in module.vocabulary] if hasattr(module, "vocabulary") else [],
            }
        )

    if result.vocabulary:
        vocabulary = [w.model_dump() for w in result.vocabulary]

    analysis_metadata = {
        "module_count": result.module_count,
        "total_vocabulary": result.total_vocabulary,
        "total_topics": result.total_topics,
        "primary_language": result.primary_language,
        "translation_language": result.translation_language,
        "difficulty_range": result.difficulty_range,
        "method": result.method,
        "processing_time_seconds": result.processing_time_seconds,
    }

    ai_storage.save_analysis_result(
        teacher_id=teacher_id,
        material_name=material_name,
        modules=modules,
        vocabulary=vocabulary,
        analysis_metadata=analysis_metadata,
    )

    # Report final progress
    await progress.report_progress("material_analysis", 100)

    logger.info(
        "Material analysis completed: %d modules, %d vocabulary words",
        result.module_count,
        result.total_vocabulary,
    )

    return {
        "module_count": result.module_count,
        "total_vocabulary": result.total_vocabulary,
        "total_topics": result.total_topics,
        "primary_language": result.primary_language,
        "processing_time_seconds": result.processing_time_seconds,
    }


async def _run_material_audio_generation(
    job_id: str,
    material_id: int,
    teacher_id: str,
    material_name: str,
    progress: ProgressReporter,
    analysis_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run material audio generation stage.

    Args:
        job_id: Job ID
        material_id: Material database ID
        teacher_id: Teacher ID
        material_name: Material filename
        progress: Progress reporter
        analysis_result: Result from AI analysis

    Returns:
        Audio generation result data
    """
    from app.services.audio_generation import get_audio_generation_service
    from app.services.material_ai_data import get_material_ai_storage

    logger.info(
        "Starting material audio generation for %s (teacher: %s)",
        material_name,
        teacher_id,
    )

    ai_storage = get_material_ai_storage()
    audio_service = get_audio_generation_service()

    # Load vocabulary
    vocab_data = ai_storage.load_vocabulary(teacher_id, material_name)
    vocabulary_words = vocab_data.get("words", [])

    if not vocabulary_words:
        logger.info("No vocabulary words to generate audio for")
        await progress.report_progress("material_audio", 100)
        return {
            "total_words": 0,
            "generated_count": 0,
            "failed_count": 0,
        }

    primary_language = vocab_data.get("language", "en")
    translation_language = vocab_data.get("translation_language", "tr")

    # Report initial progress
    await progress.report_progress("material_audio", 10)

    # Generate audio
    result, audio_data = await audio_service.generate_vocabulary_audio(
        vocabulary=vocabulary_words,
        book_id=str(material_id),
        publisher_id=teacher_id,
        book_name=material_name,
        language=primary_language,
        translation_language=translation_language,
        progress_callback=None,
    )

    # Report progress at 50%
    await progress.report_progress("material_audio", 50)

    # Save audio files
    saved_count = 0
    failed_count = 0

    for audio_file in result.audio_files:
        word = audio_file.word
        lang = audio_file.language
        if word in audio_data:
            try:
                ai_storage.save_audio_file(
                    teacher_id=teacher_id,
                    material_name=material_name,
                    word=word,
                    language=lang,
                    audio_data=audio_data[word],
                )
                saved_count += 1
            except Exception as e:
                logger.warning("Failed to save audio for %s: %s", word, e)
                failed_count += 1

    # Report final progress
    await progress.report_progress("material_audio", 100)

    logger.info(
        "Material audio generation completed: %d/%d generated, %d saved",
        result.generated_count,
        len(vocabulary_words),
        saved_count,
    )

    return {
        "total_words": len(vocabulary_words),
        "generated_count": result.generated_count,
        "failed_count": result.failed_count,
        "audio_files_saved": saved_count,
        "language": primary_language,
        "translation_language": translation_language,
    }


# =============================================================================
# Bundle Creation Task
# =============================================================================


async def create_bundle_task(
    ctx: dict[str, Any],
    job_id: str,
    platform: str,
    book_id: int,
    publisher_id: int,
    book_name: str,
    force: bool = False,
    metadata: dict[str, Any] | None = None,
    local_book_path: str | None = None,
) -> dict[str, Any]:
    """Create a standalone app bundle asynchronously.

    This task orchestrates bundle creation including:
    - Downloading the app template
    - Extracting the template
    - Downloading book assets (or copying from local cache)
    - Creating the bundle zip
    - Uploading to MinIO

    Args:
        ctx: arq context with Redis connection
        job_id: Processing job ID
        platform: Target platform (mac, win, linux)
        book_id: Book database ID
        publisher_id: Publisher ID (integer, for storage path)
        book_name: Book name
        force: If True, recreate bundle even if it exists
        metadata: Additional job metadata
        local_book_path: Local cache dir with book assets (skips R2 download)

    Returns:
        Result dict with download_url, file_name, file_size

    Raises:
        QueueError: If bundle creation fails
    """
    import os
    import tempfile
    import zipfile
    from datetime import timedelta

    from app.services import get_minio_client, get_minio_client_external
    from app.services.standalone_apps import (
        ALLOWED_PLATFORMS,
        BUNDLE_PREFIX,
        PRESIGNED_URL_EXPIRY_SECONDS,
        TEMPLATE_PREFIX,
        InvalidPlatformError,
        TemplateNotFoundError,
    )

    settings = get_settings()
    redis_conn = await get_redis_connection(url=settings.redis_url)
    repository = JobRepository(
        redis_client=redis_conn.client,
        job_ttl_seconds=settings.queue_job_ttl_seconds,
    )

    # Helper to update progress directly (bundle uses different stages than book processing)
    async def update_progress(progress: int, step: str) -> None:
        await repository.update_job_progress(job_id, progress, current_step=step)

    logger.info(
        "Starting bundle creation job %s for book %s (platform: %s, publisher_id: %s)",
        job_id,
        book_name,
        platform,
        publisher_id,
    )

    # Update job status to processing
    await repository.update_job_status(job_id, ProcessingStatus.PROCESSING)

    try:
        # Validate platform
        normalized_platform = platform.lower()
        if normalized_platform not in ALLOWED_PLATFORMS:
            raise InvalidPlatformError(
                f"Invalid platform '{platform}'. Allowed: {', '.join(sorted(ALLOWED_PLATFORMS))}"
            )

        client = get_minio_client(settings)
        external_client = get_minio_client_external(settings)
        apps_bucket = settings.minio_apps_bucket
        publishers_bucket = settings.minio_publishers_bucket

        template_object_name = f"{TEMPLATE_PREFIX}/{normalized_platform}.zip"

        # Check template exists
        await update_progress(5, "Checking template...")
        try:
            client.stat_object(apps_bucket, template_object_name)
        except Exception as exc:
            raise TemplateNotFoundError(f"Template for platform '{normalized_platform}' not found") from exc

        # Check if bundle already exists (unless force=True)
        if not force:
            bundle_prefix = f"{BUNDLE_PREFIX}/{publisher_id}/{book_name}/"
            try:
                existing_bundles = list(client.list_objects(apps_bucket, prefix=bundle_prefix, recursive=True))
                for obj in existing_bundles:
                    file_name = obj.object_name.split("/")[-1]
                    if file_name.lower().startswith(f"({normalized_platform})"):
                        logger.info(
                            "Found existing bundle for %s/%s platform %s",
                            publisher_id,
                            book_name,
                            normalized_platform,
                        )
                        download_url = external_client.presigned_get_object(
                            bucket_name=apps_bucket,
                            object_name=obj.object_name,
                            expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
                        )

                        await update_progress(100, "Bundle ready (cached)")
                        await repository.update_job_status(job_id, ProcessingStatus.COMPLETED)

                        return {
                            "status": "completed",
                            "cached": True,
                            "download_url": download_url,
                            "file_name": file_name,
                            "file_size": obj.size,
                        }
            except Exception as e:
                logger.warning("Failed to check bundle cache: %s", e)

        with tempfile.TemporaryDirectory() as temp_dir:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from app.services.standalone_apps import (
                ASSET_DOWNLOAD_WORKERS,
                _download_asset,
                _get_cached_template,
            )

            # 1. Get template from local cache (5-15%)
            await update_progress(10, "Loading template...")
            template_path = _get_cached_template(client, apps_bucket, template_object_name)
            await update_progress(15, "Template ready")

            # 2. Extract template (15-25%)
            await update_progress(20, "Extracting template...")
            extract_dir = os.path.join(temp_dir, "app")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(template_path, "r") as zf:
                zf.extractall(extract_dir)
            await update_progress(25, "Template extracted")

            # 3. Remove __MACOSX metadata folder if present
            macosx_dir = os.path.join(extract_dir, "__MACOSX")
            if os.path.isdir(macosx_dir):
                shutil.rmtree(macosx_dir)

            # 4. Find the app folder containing 'data' directory (may be nested)
            app_root = extract_dir
            app_folder_name = None

            for dirpath, dirnames, _files in os.walk(extract_dir):
                if "data" in dirnames:
                    app_root = dirpath
                    app_folder_name = os.path.basename(dirpath)
                    extract_dir = os.path.dirname(dirpath)
                    break

            # 4. Create book directory
            data_dir = os.path.join(app_root, "data")
            if not os.path.isdir(data_dir):
                os.makedirs(data_dir, exist_ok=True)

            book_dir = os.path.join(data_dir, "books", book_name)
            os.makedirs(book_dir, exist_ok=True)

            # 5. Get book assets: local cache (fast) or R2 download (fallback)
            use_local_cache = local_book_path and os.path.isdir(local_book_path)

            if use_local_cache:
                await update_progress(25, "Copying from local cache...")

                # Copy all files from local cache to book_dir
                asset_count = 0
                for root, _dirs, files in os.walk(local_book_path):
                    for file in files:
                        if file.startswith("."):  # Skip .platform_count etc.
                            continue
                        src = os.path.join(root, file)
                        rel = os.path.relpath(src, local_book_path)
                        dst = os.path.join(book_dir, rel)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        asset_count += 1

                if asset_count == 0:
                    logger.warning("Local cache empty for %s, falling back to R2 download", local_book_path)
                    use_local_cache = False
                else:
                    logger.info("Copied %d assets from local cache for book %s/%s", asset_count, publisher_id, book_name)
                    await update_progress(70, f"Copied {asset_count} assets from cache")

            if not use_local_cache:
                await update_progress(25, "Downloading book assets...")
                book_prefix = f"{publisher_id}/books/{book_name}/"
                objects = [
                    obj for obj in client.list_objects(publishers_bucket, prefix=book_prefix, recursive=True)
                    if not obj.is_dir and obj.object_name[len(book_prefix):]
                ]
                total_objects = len(objects)

                download_tasks = []
                for obj in objects:
                    relative_path = obj.object_name[len(book_prefix):]
                    dest_path = os.path.join(book_dir, relative_path)
                    download_tasks.append((publishers_bucket, obj.object_name, dest_path))

                asset_count = 0
                with ThreadPoolExecutor(max_workers=ASSET_DOWNLOAD_WORKERS) as executor:
                    futures = {
                        executor.submit(_download_asset, client, bucket, obj_name, dest): obj_name
                        for bucket, obj_name, dest in download_tasks
                    }
                    for future in as_completed(futures):
                        future.result()
                        asset_count += 1
                        if total_objects > 0:
                            pct = 25 + int(asset_count / total_objects * 45)
                            await update_progress(pct, f"Downloaded {asset_count}/{total_objects} assets")

                logger.info("Downloaded %d assets in parallel for book %s/%s", asset_count, publisher_id, book_name)
                await update_progress(70, f"Downloaded {asset_count} assets")

            # 6. Rename app folder and create bundle ZIP
            await update_progress(75, "Creating bundle...")
            if app_folder_name:
                bundle_name = f"{app_folder_name} - {book_name}"
            else:
                bundle_name = f"({normalized_platform}) FlowBook - {book_name}"

            # Rename the app folder so ZIP root matches bundle name
            if app_folder_name and app_folder_name != bundle_name:
                old_path = os.path.join(extract_dir, app_folder_name)
                new_path = os.path.join(extract_dir, bundle_name)
                os.rename(old_path, new_path)

            bundle_path = os.path.join(temp_dir, f"{bundle_name}.zip")

            with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_STORED) as zf:
                for root, _dirs, files in os.walk(extract_dir):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, extract_dir)
                        zf.write(file_path, arcname)
            await update_progress(90, "Bundle created")

            # 7. Upload bundle (90-100%)
            await update_progress(92, "Uploading bundle...")
            bundle_object_name = f"{BUNDLE_PREFIX}/{publisher_id}/{book_name}/{bundle_name}.zip"
            bundle_size = os.path.getsize(bundle_path)

            client.fput_object(
                apps_bucket,
                bundle_object_name,
                bundle_path,
                content_type="application/zip",
            )
            await update_progress(98, "Bundle uploaded")

            # 8. Generate presigned URL
            download_url = external_client.presigned_get_object(
                bucket_name=apps_bucket,
                object_name=bundle_object_name,
                expires=timedelta(seconds=PRESIGNED_URL_EXPIRY_SECONDS),
            )

            await update_progress(100, "Bundle ready")

            # Store download_url in job hash so bundle-status can return it
            job_key = f"dcs:job:{job_id}"
            await repository._redis.hset(job_key, "download_url", download_url)

            await repository.update_job_status(job_id, ProcessingStatus.COMPLETED)

            logger.info(
                "Bundle creation completed: %s (%d bytes)",
                bundle_name,
                bundle_size,
            )

            return {
                "status": "completed",
                "cached": False,
                "download_url": download_url,
                "file_name": f"{bundle_name}.zip",
                "file_size": bundle_size,
            }

    except Exception as e:
        logger.error("Bundle creation job %s failed: %s", job_id, e)
        await repository.update_job_status(
            job_id,
            ProcessingStatus.FAILED,
            error_message=str(e),
        )
        raise QueueError(f"Bundle creation failed: {e}") from e
    finally:
        # Decrement platform counter and clean up local cache when last platform finishes
        if local_book_path and os.path.isdir(local_book_path):
            count_file = os.path.join(local_book_path, ".platform_count")
            try:
                with open(count_file, "r+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    count = int(f.read().strip()) - 1
                    if count <= 0:
                        fcntl.flock(f, fcntl.LOCK_UN)
                        f.close()
                        shutil.rmtree(local_book_path, ignore_errors=True)
                        logger.info("Cleaned up local book cache: %s", local_book_path)
                    else:
                        f.seek(0)
                        f.write(str(count))
                        f.truncate()
                        fcntl.flock(f, fcntl.LOCK_UN)
            except FileNotFoundError:
                logger.debug("Cache already cleaned up: %s", local_book_path)
            except Exception as cleanup_err:
                logger.warning("Failed to clean up book cache %s: %s", local_book_path, cleanup_err)
