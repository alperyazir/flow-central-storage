"""PDF Extraction Service - main entry point for book PDF processing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from minio.error import S3Error

from app.core.config import get_settings
from app.services.minio import get_minio_client
from app.services.pdf.detector import ScannedPDFDetector
from app.services.pdf.extractor import PDFExtractor
from app.services.pdf.models import (
    ExtractionMethod,
    PageText,
    PDFExtractionResult,
    PDFNotFoundError,
    PDFPageLimitExceededError,
)
from app.services.pdf.ocr import PDFOCRService

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.core.config import Settings

logger = logging.getLogger(__name__)


class PDFExtractionService:
    """
    Unified PDF extraction service.

    Coordinates native text extraction, scanned page detection,
    and OCR fallback for complete PDF text extraction.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        """
        Initialize the PDF extraction service.

        Args:
            settings: Application settings. If not provided, will load from environment.
        """
        self.settings = settings or get_settings()

    def _build_pdf_path(
        self,
        publisher_slug: str,
        book_id: str,
        book_name: str,
    ) -> str:
        """
        Build the MinIO path to the book's PDF file.

        Args:
            publisher_slug: Publisher slug.
            book_id: Book identifier.
            book_name: Book name (folder name).

        Returns:
            MinIO object path.
        """
        return f"{publisher_slug}/books/{book_name}/raw/original.pdf"

    def _download_pdf(self, pdf_path: str, book_id: str) -> bytes:
        """
        Download PDF from MinIO storage.

        Args:
            pdf_path: Path to PDF in MinIO.
            book_id: Book identifier for error reporting.

        Returns:
            PDF file bytes.

        Raises:
            PDFNotFoundError: If PDF does not exist.
        """
        client = get_minio_client(self.settings)
        bucket = self.settings.minio_publishers_bucket

        try:
            response = client.get_object(bucket, pdf_path)
            pdf_data = response.read()
            response.close()
            response.release_conn()
            return pdf_data
        except S3Error as e:
            if e.code == "NoSuchKey":
                raise PDFNotFoundError(book_id, pdf_path) from e
            raise

    async def extract_book_pdf(
        self,
        book_id: str,
        publisher_id: str,
        book_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> PDFExtractionResult:
        """
        Extract text from a book's PDF.

        Main entry point for PDF text extraction. Handles:
        - PDF download from MinIO
        - Native text extraction
        - Scanned page detection
        - OCR fallback for scanned pages
        - Progress reporting

        Args:
            book_id: Book identifier.
            publisher_id: Publisher identifier.
            book_name: Book name (folder name in storage).
            progress_callback: Optional callback(current_page, total_pages).

        Returns:
            PDFExtractionResult with all extracted text.

        Raises:
            PDFNotFoundError: If PDF is not in storage.
            PDFPasswordProtectedError: If PDF requires password.
            PDFCorruptedError: If PDF is corrupted.
            PDFPageLimitExceededError: If PDF exceeds max page limit.
        """
        logger.info(
            "Starting PDF extraction for book %s (publisher: %s, name: %s)",
            book_id,
            publisher_id,
            book_name,
        )

        # Download PDF from MinIO
        pdf_path = self._build_pdf_path(publisher_id, book_id, book_name)
        pdf_data = self._download_pdf(pdf_path, book_id)
        logger.debug("Downloaded PDF: %d bytes", len(pdf_data))

        # Extract text
        with PDFExtractor(pdf_data, book_id) as extractor:
            # Check page limit
            if extractor.page_count > self.settings.pdf_max_pages:
                raise PDFPageLimitExceededError(
                    book_id,
                    extractor.page_count,
                    self.settings.pdf_max_pages,
                )

            total_pages = extractor.page_count
            logger.info("PDF has %d pages", total_pages)

            # First pass: extract all pages natively
            native_pages = extractor.extract_all_pages(progress_callback=None)

            # Detect scanned pages
            detector = ScannedPDFDetector(
                min_char_threshold=self.settings.pdf_min_text_threshold,
                min_word_threshold=self.settings.pdf_min_word_threshold,
            )

            analysis = detector.analyze_page_texts([p.text for p in native_pages])
            logger.info(
                "Analysis: %d native, %d scanned pages (%s)",
                analysis.native_pages,
                analysis.scanned_pages,
                analysis.classification.value,
            )

            # Determine if OCR is needed
            final_pages: list[PageText] = list(native_pages)
            ocr_performed = False

            if analysis.scanned_pages > 0 and self.settings.pdf_ocr_enabled:
                logger.info(
                    "OCR enabled, processing %d scanned pages",
                    analysis.scanned_pages,
                )

                # Get 0-indexed page numbers that need OCR
                scanned_indices = [pn - 1 for pn in analysis.scanned_page_numbers]

                # OCR scanned pages
                ocr_service = PDFOCRService(
                    book_id=book_id,
                    dpi=self.settings.pdf_ocr_dpi,
                    batch_size=self.settings.pdf_ocr_batch_size,
                )

                # Track OCR progress within overall progress
                ocr_completed = 0

                def ocr_progress(current: int, total: int) -> None:
                    nonlocal ocr_completed
                    ocr_completed = current
                    if progress_callback:
                        # Combine native + OCR progress
                        # Native extraction is instant, so report OCR progress
                        progress_callback(
                            analysis.native_pages + current,
                            total_pages,
                        )

                ocr_pages = await ocr_service.ocr_pages(
                    extractor,
                    scanned_indices,
                    progress_callback=ocr_progress,
                )

                # Replace scanned pages with OCR results
                for ocr_page in ocr_pages:
                    idx = ocr_page.page_number - 1  # Convert to 0-indexed
                    final_pages[idx] = ocr_page

                ocr_performed = True

            # Report final progress
            if progress_callback:
                progress_callback(total_pages, total_pages)

            # Determine final method
            if not ocr_performed:
                method = ExtractionMethod.NATIVE
            elif analysis.native_pages == 0:
                method = ExtractionMethod.OCR
            else:
                method = ExtractionMethod.MIXED

            result = PDFExtractionResult(
                book_id=book_id,
                publisher_id=publisher_id,
                book_name=book_name,
                total_pages=total_pages,
                pages=final_pages,
                method=method,
                scanned_page_count=analysis.scanned_pages if ocr_performed else 0,
                native_page_count=analysis.native_pages if ocr_performed else total_pages,
            )

            logger.info(
                "Extraction complete: %d pages, %d words, method=%s",
                result.total_pages,
                result.total_word_count,
                result.method.value,
            )

            return result

    async def extract_from_bytes(
        self,
        pdf_data: bytes,
        book_id: str,
        publisher_id: str,
        book_name: str,
        progress_callback: Callable[[int, int], None] | None = None,
    ) -> PDFExtractionResult:
        """
        Extract text from PDF bytes directly.

        Useful for testing or when PDF is already in memory.

        Args:
            pdf_data: PDF file bytes.
            book_id: Book identifier.
            publisher_id: Publisher identifier.
            book_name: Book name.
            progress_callback: Optional progress callback.

        Returns:
            PDFExtractionResult with all extracted text.
        """
        with PDFExtractor(pdf_data, book_id) as extractor:
            # Check page limit
            if extractor.page_count > self.settings.pdf_max_pages:
                raise PDFPageLimitExceededError(
                    book_id,
                    extractor.page_count,
                    self.settings.pdf_max_pages,
                )

            total_pages = extractor.page_count

            # Extract all pages natively
            native_pages = extractor.extract_all_pages(progress_callback=None)

            # Detect scanned pages
            detector = ScannedPDFDetector(
                min_char_threshold=self.settings.pdf_min_text_threshold,
                min_word_threshold=self.settings.pdf_min_word_threshold,
            )
            analysis = detector.analyze_page_texts([p.text for p in native_pages])

            final_pages: list[PageText] = list(native_pages)
            ocr_performed = False

            if analysis.scanned_pages > 0 and self.settings.pdf_ocr_enabled:
                scanned_indices = [pn - 1 for pn in analysis.scanned_page_numbers]

                ocr_service = PDFOCRService(
                    book_id=book_id,
                    dpi=self.settings.pdf_ocr_dpi,
                    batch_size=self.settings.pdf_ocr_batch_size,
                )

                ocr_pages = await ocr_service.ocr_pages(
                    extractor,
                    scanned_indices,
                    progress_callback=progress_callback,
                )

                for ocr_page in ocr_pages:
                    idx = ocr_page.page_number - 1
                    final_pages[idx] = ocr_page

                ocr_performed = True

            if progress_callback:
                progress_callback(total_pages, total_pages)

            method = ExtractionMethod.NATIVE
            if ocr_performed:
                method = ExtractionMethod.MIXED if analysis.native_pages > 0 else ExtractionMethod.OCR

            return PDFExtractionResult(
                book_id=book_id,
                publisher_id=publisher_id,
                book_name=book_name,
                total_pages=total_pages,
                pages=final_pages,
                method=method,
                scanned_page_count=analysis.scanned_pages if ocr_performed else 0,
                native_page_count=analysis.native_pages if ocr_performed else total_pages,
            )


# Singleton instance
_extraction_service: PDFExtractionService | None = None


def get_extraction_service() -> PDFExtractionService:
    """Get or create the global PDF extraction service instance."""
    global _extraction_service
    if _extraction_service is None:
        _extraction_service = PDFExtractionService()
    return _extraction_service
