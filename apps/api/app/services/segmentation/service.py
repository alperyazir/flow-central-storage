"""Unified segmentation service for AI book processing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable

from app.core.config import get_settings
from app.services.segmentation.models import (
    ManualModuleDefinition,
    Module,
    ModuleBoundary,
    NoTextFoundError,
    SegmentationLimitError,
    SegmentationMethod,
    SegmentationResult,
)
from app.services.segmentation.strategies.ai import AIAssistedStrategy
from app.services.segmentation.strategies.fallback import (
    PageSplitStrategy,
    SingleModuleStrategy,
)
from app.services.segmentation.strategies.header import HeaderBasedStrategy
from app.services.segmentation.strategies.manual import ManualStrategy
from app.services.segmentation.strategies.toc import TOCBasedStrategy

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.services.pdf.storage import AIDataStorage

logger = logging.getLogger(__name__)


class SegmentationService:
    """
    Unified service for book segmentation.

    Coordinates multiple segmentation strategies with automatic
    fallback and handles storage integration.

    Strategy selection order:
    1. Manual (if definitions provided)
    2. TOC-based
    3. Header-based
    4. AI-assisted
    5. Fallback (single module or page split)
    """

    def __init__(
        self,
        settings: Settings | None = None,
        ai_storage: AIDataStorage | None = None,
    ) -> None:
        """
        Initialize segmentation service.

        Args:
            settings: Application settings.
            ai_storage: Storage service for loading text.
        """
        self.settings = settings or get_settings()
        self._ai_storage = ai_storage

        # Initialize strategies
        self._header_strategy = HeaderBasedStrategy()
        self._toc_strategy = TOCBasedStrategy()
        self._ai_strategy = AIAssistedStrategy()
        self._manual_strategy = ManualStrategy()
        self._single_module_strategy = SingleModuleStrategy()
        self._page_split_strategy = PageSplitStrategy()

    @property
    def ai_storage(self) -> AIDataStorage:
        """Get AI storage service (lazy load)."""
        if self._ai_storage is None:
            from app.services.pdf.storage import get_ai_storage

            self._ai_storage = get_ai_storage()
        return self._ai_storage

    async def segment_book(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        manual_definitions: list[ManualModuleDefinition] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> SegmentationResult:
        """
        Segment a book into modules.

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher identifier.
            book_name: Book folder name.
            manual_definitions: Optional manual module definitions.
            progress_callback: Optional progress callback (current, total).

        Returns:
            SegmentationResult with detected modules.

        Raises:
            NoTextFoundError: If no extracted text found.
            SegmentationError: If segmentation fails.
        """
        logger.info(
            "Starting segmentation for book %s (publisher: %s)",
            book_id,
            publisher_slug,
        )

        # Report initial progress
        if progress_callback:
            progress_callback(0, 100)

        # Load extracted text from ai-data/text/
        pages = await self._load_text_pages(publisher_slug, book_id, book_name)

        if not pages:
            raise NoTextFoundError(
                book_id,
                f"{publisher_slug}/books/{book_name}/ai-data/text/",
            )

        total_pages = max(pages.keys())
        logger.info("Loaded %d pages for segmentation", len(pages))

        if progress_callback:
            progress_callback(20, 100)

        # Try segmentation strategies in order
        boundaries, method = await self._detect_boundaries(
            pages=pages,
            book_id=book_id,
            manual_definitions=manual_definitions,
            progress_callback=progress_callback,
        )

        if progress_callback:
            progress_callback(60, 100)

        # Validate module count
        max_modules = self.settings.segmentation_max_modules
        if len(boundaries) > max_modules:
            raise SegmentationLimitError(book_id, len(boundaries), max_modules)

        # Build modules from boundaries
        modules = self._build_modules(
            boundaries=boundaries,
            pages=pages,
            total_pages=total_pages,
        )

        if progress_callback:
            progress_callback(90, 100)

        result = SegmentationResult(
            book_id=book_id,
            publisher_id=publisher_slug,
            book_name=book_name,
            total_pages=total_pages,
            modules=modules,
            method=method,
        )

        logger.info(
            "Segmentation complete: %d modules using %s method",
            len(modules),
            method.value,
        )

        if progress_callback:
            progress_callback(100, 100)

        return result

    async def _load_text_pages(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> dict[int, str]:
        """Load extracted text pages from storage."""
        pages: dict[int, str] = {}

        # Get metadata to know how many pages
        metadata = self.ai_storage.get_extraction_metadata(publisher_slug, book_id, book_name)

        if not metadata:
            logger.warning("No extraction metadata found for book %s", book_id)
            return pages

        total_pages = metadata.get("total_pages", 0)

        # Load each page
        for page_num in range(1, total_pages + 1):
            text = self._load_page_text(publisher_slug, book_id, book_name, page_num)
            if text:
                pages[page_num] = text

        return pages

    def _load_page_text(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
        page_num: int,
    ) -> str | None:
        """Load a single page's text from storage."""
        from minio.error import S3Error

        from app.services.minio import get_minio_client

        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        # Build path (book_id not used in storage path)
        path = f"{publisher_slug}/books/{book_name}/ai-data/text/page_{page_num:03d}.txt"

        try:
            response = client.get_object(bucket, path)
            data = response.read()
            response.close()
            response.release_conn()
            return data.decode("utf-8")
        except S3Error as e:
            if e.code == "NoSuchKey":
                return None
            raise

    def _is_poor_quality_segmentation(
        self,
        boundaries: list[ModuleBoundary],
        total_pages: int,
    ) -> bool:
        """
        Check if segmentation result is poor quality.

        Poor quality indicators:
        - Any module would have more than max_module_pages
        - Too many empty/small modules (same start page)
        """
        if len(boundaries) < 2:
            return True

        max_pages = self.settings.segmentation_max_module_pages
        sorted_bounds = sorted(boundaries, key=lambda b: b.start_page)

        # Check for oversized modules
        for i, boundary in enumerate(sorted_bounds):
            if i + 1 < len(sorted_bounds):
                end_page = sorted_bounds[i + 1].start_page - 1
            else:
                end_page = total_pages

            module_size = end_page - boundary.start_page + 1
            if module_size > max_pages:
                logger.debug(
                    "Poor quality: module '%s' has %d pages (max: %d)",
                    boundary.title,
                    module_size,
                    max_pages,
                )
                return True

        # Check for too many empty modules (same start page)
        empty_count = 0
        for i, boundary in enumerate(sorted_bounds[:-1]):
            next_start = sorted_bounds[i + 1].start_page
            if boundary.start_page >= next_start:
                empty_count += 1

        if empty_count > len(boundaries) // 3:  # More than 1/3 empty
            logger.debug(
                "Poor quality: %d/%d modules would be empty",
                empty_count,
                len(boundaries),
            )
            return True

        return False

    async def _detect_boundaries(
        self,
        pages: dict[int, str],
        book_id: str,
        manual_definitions: list[ManualModuleDefinition] | None = None,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> tuple[list[ModuleBoundary], SegmentationMethod]:
        """Try strategies in order until one succeeds."""
        total_pages = max(pages.keys()) if pages else 0

        # 1. Manual definitions (highest priority)
        if manual_definitions:
            logger.debug("Trying manual segmentation")
            self._manual_strategy.definitions = manual_definitions
            boundaries = self._manual_strategy.detect_boundaries(pages, book_id=book_id)
            if boundaries:
                return boundaries, SegmentationMethod.MANUAL

        if progress_callback:
            progress_callback(30, 100)

        # 2. TOC-based
        logger.debug("Trying TOC-based segmentation")
        toc_boundaries = self._toc_strategy.detect_boundaries(pages)
        if len(toc_boundaries) >= 2:
            if not self._is_poor_quality_segmentation(toc_boundaries, total_pages):
                return toc_boundaries, SegmentationMethod.TOC_BASED
            logger.info("TOC-based segmentation has poor quality, trying alternatives")

        if progress_callback:
            progress_callback(40, 100)

        # 3. Header-based
        logger.debug("Trying header-based segmentation")
        header_boundaries = self._header_strategy.detect_boundaries(pages)
        if len(header_boundaries) >= 2:
            if not self._is_poor_quality_segmentation(header_boundaries, total_pages):
                return header_boundaries, SegmentationMethod.HEADER_BASED
            logger.info("Header-based segmentation has poor quality, trying AI")

        if progress_callback:
            progress_callback(50, 100)

        # 4. AI-assisted (if enabled) - try if no good results yet or as fallback
        use_ai = self.settings.segmentation_ai_enabled and (
            self.settings.segmentation_ai_fallback_on_poor_quality or len(header_boundaries) < 2
        )

        if use_ai:
            logger.debug("Trying AI-assisted segmentation")
            try:
                boundaries = await self._ai_strategy.detect_boundaries_async(pages)
                if len(boundaries) >= 2:
                    return boundaries, SegmentationMethod.AI_ASSISTED
            except Exception as e:
                logger.warning("AI segmentation failed: %s", e)

        # 5. Use best non-AI result if available (even if poor quality)
        if len(header_boundaries) >= 2:
            logger.warning("Using header-based segmentation despite poor quality (AI unavailable or failed)")
            return header_boundaries, SegmentationMethod.HEADER_BASED
        if len(toc_boundaries) >= 2:
            logger.warning("Using TOC-based segmentation despite poor quality (AI unavailable or failed)")
            return toc_boundaries, SegmentationMethod.TOC_BASED

        # 6. Fallback - single module or page split
        min_pages = self.settings.segmentation_min_module_pages

        if total_pages > min_pages * 3:
            # Large book - split by pages
            logger.debug("Using page split fallback")
            boundaries = self._page_split_strategy.detect_boundaries(pages)
            return boundaries, SegmentationMethod.PAGE_SPLIT
        else:
            # Small book - single module
            logger.debug("Using single module fallback")
            boundaries = self._single_module_strategy.detect_boundaries(pages)
            return boundaries, SegmentationMethod.SINGLE_MODULE

    def _build_modules(
        self,
        boundaries: list[ModuleBoundary],
        pages: dict[int, str],
        total_pages: int,
    ) -> list[Module]:
        """Build Module objects from boundaries."""
        modules: list[Module] = []

        # Sort boundaries by start page
        sorted_boundaries = sorted(boundaries, key=lambda b: b.start_page)

        # Filter out boundaries that would create empty modules
        # (multiple headers on the same page like TOC listings)
        filtered_boundaries: list[ModuleBoundary] = []
        for i, boundary in enumerate(sorted_boundaries):
            # Check if this boundary has a different start page than the next one
            if i + 1 < len(sorted_boundaries):
                next_start = sorted_boundaries[i + 1].start_page
                # Only keep if it actually covers at least one page
                if boundary.start_page < next_start:
                    filtered_boundaries.append(boundary)
                else:
                    logger.debug(
                        "Skipping boundary '%s' at page %d (same page as next)",
                        boundary.title,
                        boundary.start_page,
                    )
            else:
                # Last boundary - always keep
                filtered_boundaries.append(boundary)

        # Re-sort after filtering (should already be sorted but be safe)
        filtered_boundaries.sort(key=lambda b: b.start_page)

        for i, boundary in enumerate(filtered_boundaries):
            start_page = boundary.start_page

            # End page is one before next boundary, or last page
            if i + 1 < len(filtered_boundaries):
                end_page = filtered_boundaries[i + 1].start_page - 1
            else:
                end_page = total_pages

            # Collect pages for this module
            module_pages = list(range(start_page, end_page + 1))

            # Skip if no pages (shouldn't happen after filtering, but be safe)
            if not module_pages:
                logger.warning(
                    "Module '%s' has no pages, skipping",
                    boundary.title,
                )
                continue

            # Collect text
            text_parts = []
            for page_num in module_pages:
                if page_num in pages:
                    text_parts.append(pages[page_num])

            text = "\n\n".join(text_parts)

            module = Module(
                module_id=len(modules) + 1,  # Sequential ID after filtering
                title=boundary.title,
                pages=module_pages,
                start_page=start_page,
                end_page=end_page,
                text=text,
            )
            modules.append(module)

        return modules

    async def segment_from_text(
        self,
        book_id: str,
        publisher_slug: str,
        book_name: str,
        pages: dict[int, str],
        manual_definitions: list[ManualModuleDefinition] | None = None,
    ) -> SegmentationResult:
        """
        Segment from provided text (for testing or direct use).

        Args:
            book_id: Book identifier.
            publisher_slug: Publisher identifier.
            book_name: Book folder name.
            pages: Dictionary of page texts.
            manual_definitions: Optional manual definitions.

        Returns:
            SegmentationResult.
        """
        if not pages:
            raise NoTextFoundError(book_id, "provided pages dict")

        total_pages = max(pages.keys())

        boundaries, method = await self._detect_boundaries(
            pages=pages,
            book_id=book_id,
            manual_definitions=manual_definitions,
        )

        modules = self._build_modules(
            boundaries=boundaries,
            pages=pages,
            total_pages=total_pages,
        )

        return SegmentationResult(
            book_id=book_id,
            publisher_id=publisher_slug,
            book_name=book_name,
            total_pages=total_pages,
            modules=modules,
            method=method,
        )


# Singleton instance
_segmentation_service: SegmentationService | None = None


def get_segmentation_service() -> SegmentationService:
    """Get or create the global segmentation service instance."""
    global _segmentation_service
    if _segmentation_service is None:
        _segmentation_service = SegmentationService()
    return _segmentation_service
