"""A chunked run must not report success over an empty result.

Seen in production on the Harvest_Practice_Tests_*, Klar_Doch_* and
The_Chase_7_Practice_Book books: Phase 2 failed for every module, the service
carried on with empty vocabulary, and the job ended "completed" with
total_vocabulary 0 and not a single audio file. The badge was green over
nothing, and because the dashboard filters on that status the books never came
back for a retry.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.queue.models import QueueError
from app.services.queue.tasks import _degraded_stage_errors, _run_chunked_analysis
from app.services.unified_analysis.models import UnifiedAnalysisResult
from app.services.unified_analysis.service import UnifiedAnalysisService

MODULES = {
    "language": "en",
    "modules": [
        {"title": "Unit 1", "start_page": 1, "end_page": 10, "topics": ["food"]},
        {"title": "Unit 2", "start_page": 11, "end_page": 20, "topics": ["travel"]},
    ],
}

VOCAB = {
    "summary": "A unit.",
    "grammar_points": ["present simple"],
    "vocabulary": [
        {"word": "bread", "definition": "food", "translation": "ekmek"},
    ],
}


def _service() -> UnifiedAnalysisService:
    service = UnifiedAnalysisService(llm_service=MagicMock())
    service.llm_service.primary_provider.default_model = "deepseek-chat"
    return service


class TestFailedModulesAreRemembered:
    """The service keeps the module but records that its words never arrived."""

    @pytest.mark.asyncio
    async def test_every_module_failing_is_recorded(self) -> None:
        service = _service()
        with patch.object(service, "_phase1_detect_modules", new=AsyncMock(return_value=MODULES)), patch.object(
            service, "_phase2_extract_vocabulary", new=AsyncMock(side_effect=ValueError("bad JSON"))
        ):
            result = await service.analyze_book_chunked(
                book_id="1", publisher_slug="edulink", book_name="B", pages={1: "text"}
            )

        assert result.failed_modules == ["Unit 1", "Unit 2"]
        assert result.total_vocabulary == 0
        # The modules survive: their structure is still worth keeping.
        assert result.module_count == 2

    @pytest.mark.asyncio
    async def test_one_module_failing_is_recorded(self) -> None:
        service = _service()
        with patch.object(service, "_phase1_detect_modules", new=AsyncMock(return_value=MODULES)), patch.object(
            service,
            "_phase2_extract_vocabulary",
            new=AsyncMock(side_effect=[VOCAB, ValueError("bad JSON"), ValueError("x"), ValueError("x")]),
        ):
            result = await service.analyze_book_chunked(
                book_id="1", publisher_slug="edulink", book_name="B", pages={1: "text"}
            )

        assert result.failed_modules == ["Unit 2"]
        assert result.total_vocabulary == 1

    @pytest.mark.asyncio
    async def test_a_clean_run_records_nothing(self) -> None:
        service = _service()
        with patch.object(service, "_phase1_detect_modules", new=AsyncMock(return_value=MODULES)), patch.object(
            service, "_phase2_extract_vocabulary", new=AsyncMock(return_value=VOCAB)
        ):
            result = await service.analyze_book_chunked(
                book_id="1", publisher_slug="edulink", book_name="B", pages={1: "text"}
            )

        assert result.failed_modules == []
        assert result.total_vocabulary == 2

    def test_failed_modules_reach_the_stored_json(self) -> None:
        result = UnifiedAnalysisResult(
            book_id="1",
            publisher_slug="edulink",
            book_name="B",
            total_pages=10,
            failed_modules=["Unit 2"],
        )
        assert result.to_dict()["failed_modules"] == ["Unit 2"]


class TestStageOutcome:
    """The stage's own verdict on what the analysis produced."""

    async def _run(self, result: UnifiedAnalysisResult):
        storage = MagicMock()
        storage.save_all.return_value = {"module_count": result.module_count, "vocabulary_count": 0}
        service = MagicMock()
        service.analyze_book_chunked = AsyncMock(return_value=result)

        with patch(
            "app.services.unified_analysis.get_unified_analysis_service", return_value=service
        ), patch(
            "app.services.unified_analysis.get_unified_analysis_storage", return_value=storage
        ), patch(
            "app.services.queue.tasks._load_text_pages_for_analysis",
            new=AsyncMock(return_value={1: "text"}),
        ):
            return await _run_chunked_analysis(
                job_id="j1",
                book_id="285",
                publisher_slug="universal-elt",
                book_name="Harvest_Practice_Tests_1",
                progress=AsyncMock(),
            )

    def _result(self, vocab_words: int, failed: list[str]) -> UnifiedAnalysisResult:
        from app.services.unified_analysis.models import AnalyzedModule, VocabularyWord

        module = AnalyzedModule(
            module_id=1,
            title="Unit 1",
            start_page=1,
            end_page=10,
            vocabulary=[
                VocabularyWord(word=f"w{i}", definition="d", translation="t") for i in range(vocab_words)
            ],
        )
        return UnifiedAnalysisResult(
            book_id="285",
            publisher_slug="universal-elt",
            book_name="Harvest_Practice_Tests_1",
            total_pages=28,
            modules=[module],
            failed_modules=failed,
        )

    @pytest.mark.asyncio
    async def test_no_vocabulary_at_all_fails_the_stage(self) -> None:
        """The exact production case: 1 module, 0 words, reported completed."""
        with pytest.raises(QueueError, match="no vocabulary"):
            await self._run(self._result(vocab_words=0, failed=["Unit 1"]))

    @pytest.mark.asyncio
    async def test_partial_vocabulary_is_reported_but_kept(self) -> None:
        stage_result = await self._run(self._result(vocab_words=5, failed=["Unit 2"]))

        assert stage_result["failed_modules"] == ["Unit 2"]
        assert stage_result["total_vocabulary"] == 5

    @pytest.mark.asyncio
    async def test_a_clean_run_carries_no_failures(self) -> None:
        stage_result = await self._run(self._result(vocab_words=5, failed=[]))

        assert stage_result["failed_modules"] == []


class TestDegradedStageErrors:
    """Losing part of a stage's output lands the run on "partial"."""

    def test_missing_vocabulary_is_reported_like_a_stage_failure(self) -> None:
        errors = _degraded_stage_errors(
            {"chunked_analysis": {"failed_modules": ["Unit 2", "Unit 5"]}}
        )

        assert len(errors) == 1
        assert errors[0]["stage"] == "chunked_analysis"
        assert "2 module(s)" in errors[0]["error"]
        assert "Unit 2, Unit 5" in errors[0]["error"]

    def test_a_clean_run_reports_nothing(self) -> None:
        assert _degraded_stage_errors({"chunked_analysis": {"failed_modules": []}}) == []
        assert _degraded_stage_errors({"text_extraction": {"total_pages": 28}}) == []

    def test_stage_results_that_are_not_dicts_are_skipped(self) -> None:
        assert _degraded_stage_errors({"audio_generation": None, "segmentation": 5}) == []
