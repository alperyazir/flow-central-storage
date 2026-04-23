"""Tests for the vocabulary extraction service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.vocabulary_extraction.models import (
    BookVocabularyResult,
    DuplicateVocabularyError,
    InvalidLLMResponseError,
    LLMExtractionError,
    ModuleVocabularyResult,
    NoModulesFoundError,
    PartOfSpeech,
    VocabularyExtractionError,
    VocabularyWord,
)
from app.services.vocabulary_extraction.prompts import (
    SYSTEM_PROMPT,
    build_bilingual_vocabulary_prompt,
    build_simple_vocabulary_prompt,
    build_vocabulary_extraction_prompt,
)
from app.services.vocabulary_extraction.service import VocabularyExtractionService
from app.services.vocabulary_extraction.storage import VocabularyStorage

# =============================================================================
# Test Data Models
# =============================================================================


class TestVocabularyWord:
    """Tests for VocabularyWord dataclass."""

    def test_create_vocabulary_word(self):
        """Test creating a vocabulary word."""
        word = VocabularyWord(
            word="beautiful",
            translation="güzel",
            definition="pleasing to the senses",
            part_of_speech="adjective",
            level="A2",
            example="It's a beautiful day.",
            module_id=1,
            page=5,
        )
        assert word.word == "beautiful"
        assert word.translation == "güzel"
        assert word.part_of_speech == "adjective"
        assert word.level == "A2"
        assert word.module_id == 1

    def test_auto_generate_id(self):
        """Test that ID is auto-generated from word."""
        word = VocabularyWord(word="Hello World")
        assert word.id == "hello_world"

    def test_id_preserves_if_provided(self):
        """Test that provided ID is preserved."""
        word = VocabularyWord(word="test", id="custom_id")
        assert word.id == "custom_id"

    def test_slugify_special_chars(self):
        """Test ID generation with special characters."""
        word = VocabularyWord(word="can't stop!")
        assert word.id == "can_t_stop"

    def test_to_dict(self):
        """Test converting vocabulary word to dictionary."""
        word = VocabularyWord(
            word="example",
            translation="örnek",
            definition="a thing serving as a model",
            part_of_speech="noun",
            level="B1",
            example="This is an example.",
            module_id=2,
            page=10,
            audio={"word": "audio/en/example.mp3"},
        )
        d = word.to_dict()
        assert d["word"] == "example"
        assert d["translation"] == "örnek"
        assert d["part_of_speech"] == "noun"
        assert d["level"] == "B1"
        assert d["audio"]["word"] == "audio/en/example.mp3"

    def test_from_dict(self):
        """Test creating vocabulary word from dictionary."""
        data = {
            "id": "test_word",
            "word": "test",
            "translation": "test",
            "definition": "a test",
            "part_of_speech": "noun",
            "level": "A1",
            "example": "This is a test.",
            "module_id": 1,
            "page": 5,
            "audio": {},
        }
        word = VocabularyWord.from_dict(data)
        assert word.id == "test_word"
        assert word.word == "test"
        assert word.part_of_speech == "noun"

    def test_from_dict_with_missing_fields(self):
        """Test creating vocabulary word from partial dictionary."""
        data = {"word": "minimal"}
        word = VocabularyWord.from_dict(data)
        assert word.word == "minimal"
        assert word.translation == ""
        assert word.definition == ""
        assert word.module_id == 0


class TestModuleVocabularyResult:
    """Tests for ModuleVocabularyResult dataclass."""

    def test_create_module_vocabulary_result(self):
        """Test creating a module vocabulary result."""
        words = [
            VocabularyWord(word="hello", translation="merhaba"),
            VocabularyWord(word="goodbye", translation="güle güle"),
        ]
        result = ModuleVocabularyResult(
            module_id=1,
            module_title="Unit 1",
            words=words,
            llm_provider="deepseek",
            tokens_used=500,
            success=True,
        )
        assert result.module_id == 1
        assert result.module_title == "Unit 1"
        assert len(result.words) == 2
        assert result.llm_provider == "deepseek"
        assert result.success is True

    def test_vocabulary_ids_property(self):
        """Test vocabulary_ids property returns word IDs."""
        words = [
            VocabularyWord(word="cat"),
            VocabularyWord(word="dog"),
        ]
        result = ModuleVocabularyResult(module_id=1, words=words)
        assert result.vocabulary_ids == ["cat", "dog"]

    def test_failed_module_vocabulary_result(self):
        """Test creating a failed module vocabulary result."""
        result = ModuleVocabularyResult(
            module_id=2,
            module_title="Unit 2",
            words=[],
            success=False,
            error_message="LLM timeout",
        )
        assert result.success is False
        assert result.error_message == "LLM timeout"
        assert result.vocabulary_ids == []

    def test_to_dict(self):
        """Test converting module vocabulary result to dictionary."""
        words = [VocabularyWord(word="test")]
        result = ModuleVocabularyResult(
            module_id=1,
            module_title="Test",
            words=words,
            success=True,
        )
        d = result.to_dict()
        assert d["module_id"] == 1
        assert d["word_count"] == 1
        assert len(d["words"]) == 1


class TestBookVocabularyResult:
    """Tests for BookVocabularyResult dataclass."""

    def test_create_book_vocabulary_result(self):
        """Test creating a book vocabulary result."""
        words = [VocabularyWord(word="hello")]
        module_results = [
            ModuleVocabularyResult(module_id=1, words=words, success=True),
        ]
        result = BookVocabularyResult(
            book_id="book-123",
            publisher_id="pub-456",
            book_name="Test Book",
            language="en",
            translation_language="tr",
            words=words,
            module_results=module_results,
        )
        assert result.book_id == "book-123"
        assert result.language == "en"
        assert result.total_words == 1
        assert result.success_count == 1
        assert result.failure_count == 0

    def test_aggregate_counts(self):
        """Test that aggregate counts are calculated correctly."""
        module_results = [
            ModuleVocabularyResult(module_id=1, success=True),
            ModuleVocabularyResult(module_id=2, success=True),
            ModuleVocabularyResult(module_id=3, success=False),
        ]
        result = BookVocabularyResult(
            book_id="book-123",
            publisher_id="pub-456",
            book_name="Test",
            module_results=module_results,
        )
        assert result.success_count == 2
        assert result.failure_count == 1

    def test_to_dict_vocabulary_format(self):
        """Test to_dict produces vocabulary.json format."""
        words = [
            VocabularyWord(word="hello", translation="merhaba", level="A1"),
        ]
        result = BookVocabularyResult(
            book_id="book-123",
            publisher_id="pub-456",
            book_name="Test",
            language="en",
            translation_language="tr",
            words=words,
        )
        d = result.to_dict()
        assert d["language"] == "en"
        assert d["translation_language"] == "tr"
        assert d["total_words"] == 1
        assert len(d["words"]) == 1
        assert "extracted_at" in d


# =============================================================================
# Test Exceptions
# =============================================================================


class TestExceptions:
    """Tests for exception classes."""

    def test_vocabulary_extraction_error(self):
        """Test base vocabulary extraction error."""
        error = VocabularyExtractionError(
            message="Test error",
            book_id="book-123",
            details={"key": "value"},
        )
        assert "book-123" in str(error)
        assert "Test error" in str(error)
        assert error.details == {"key": "value"}

    def test_llm_extraction_error(self):
        """Test LLM extraction error."""
        error = LLMExtractionError(
            book_id="book-123",
            module_id=1,
            reason="Timeout",
            provider="deepseek",
        )
        assert error.module_id == 1
        assert error.reason == "Timeout"
        assert error.provider == "deepseek"

    def test_no_modules_found_error(self):
        """Test no modules found error."""
        error = NoModulesFoundError(
            book_id="book-123",
            path="/some/path",
        )
        assert error.path == "/some/path"

    def test_invalid_llm_response_error(self):
        """Test invalid LLM response error."""
        error = InvalidLLMResponseError(
            book_id="book-123",
            module_id=1,
            response="invalid json",
            parse_error="Expecting value",
        )
        assert error.module_id == 1
        assert error.parse_error == "Expecting value"

    def test_duplicate_vocabulary_error(self):
        """Test duplicate vocabulary error."""
        error = DuplicateVocabularyError(
            book_id="book-123",
            word="hello",
            module_ids=[1, 2, 3],
        )
        assert error.word == "hello"
        assert error.module_ids == [1, 2, 3]


# =============================================================================
# Test Prompts
# =============================================================================


class TestPrompts:
    """Tests for prompt templates."""

    def test_system_prompt_exists(self):
        """Test system prompt is defined."""
        assert SYSTEM_PROMPT
        assert "vocabulary" in SYSTEM_PROMPT.lower()

    def test_build_vocabulary_extraction_prompt(self):
        """Test building vocabulary extraction prompt."""
        prompt = build_vocabulary_extraction_prompt(
            module_text="This is a test text about learning.",
            difficulty="A1",
            max_words=20,
        )
        assert "This is a test text" in prompt
        assert "A1" in prompt
        assert "20" in prompt

    def test_build_vocabulary_extraction_prompt_truncation(self):
        """Test prompt truncation for long text."""
        long_text = "x" * 10000
        prompt = build_vocabulary_extraction_prompt(
            module_text=long_text,
            max_length=1000,
        )
        assert len(prompt) < len(long_text) + 1000
        assert "truncated" in prompt.lower()

    def test_build_simple_vocabulary_prompt(self):
        """Test building simple vocabulary prompt."""
        prompt = build_simple_vocabulary_prompt(
            module_text="Simple test text.",
            max_words=10,
        )
        assert "Simple test text" in prompt

    def test_build_bilingual_vocabulary_prompt(self):
        """Test building bilingual vocabulary prompt."""
        prompt = build_bilingual_vocabulary_prompt(
            module_text="Hello Merhaba",
            max_words=15,
        )
        assert "Hello Merhaba" in prompt
        assert "bilingual" in prompt.lower() or "Turkish" in prompt


# =============================================================================
# Test Vocabulary Extraction Service
# =============================================================================


class TestVocabularyExtractionService:
    """Tests for VocabularyExtractionService."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.vocabulary_max_words_per_module = 50
        settings.vocabulary_min_word_length = 3
        settings.vocabulary_temperature = 0.3
        settings.vocabulary_max_text_length = 8000
        return settings

    @pytest.fixture
    def mock_llm_service(self):
        """Create mock LLM service."""
        llm_service = MagicMock()
        llm_service.simple_completion = AsyncMock()
        llm_service.primary_provider = MagicMock()
        llm_service.primary_provider.provider_name = "deepseek"
        return llm_service

    @pytest.fixture
    def service(self, mock_settings, mock_llm_service):
        """Create service with mocked dependencies."""
        return VocabularyExtractionService(
            settings=mock_settings,
            llm_service=mock_llm_service,
        )

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_success(self, service, mock_llm_service):
        """Test successful vocabulary extraction from a module."""
        llm_response = json.dumps(
            [
                {
                    "word": "beautiful",
                    "translation": "güzel",
                    "definition": "pleasing to look at",
                    "part_of_speech": "adjective",
                    "level": "A2",
                    "example": "The sunset is beautiful.",
                },
                {
                    "word": "learn",
                    "translation": "öğrenmek",
                    "definition": "to gain knowledge",
                    "part_of_speech": "verb",
                    "level": "A1",
                    "example": "I learn English every day.",
                },
            ]
        )
        mock_llm_service.simple_completion.return_value = llm_response

        module_text = """
        This is a beautiful day for learning English.
        We will learn many new words today.
        The sunset is beautiful and peaceful.
        """

        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text=module_text,
            book_id="book-123",
            difficulty="A2",
        )

        assert result.success is True
        assert len(result.words) == 2
        assert result.words[0].word == "beautiful"
        assert result.words[0].translation == "güzel"
        assert result.words[1].word == "learn"

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_insufficient_text(self, service):
        """Test handling of insufficient text."""
        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text="Short",  # Less than 50 chars
            book_id="book-123",
        )

        assert result.success is True
        assert len(result.words) == 0
        assert "Insufficient" in result.error_message

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_empty_text(self, service):
        """Test handling of empty text."""
        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text="",
            book_id="book-123",
        )

        assert result.success is True
        assert len(result.words) == 0

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_invalid_json(self, service, mock_llm_service):
        """Test handling of invalid JSON response with fallback."""
        # First call returns invalid JSON, second returns valid
        mock_llm_service.simple_completion.side_effect = [
            "Not valid JSON at all",
            json.dumps([{"word": "test", "translation": "test", "level": "A1", "part_of_speech": "noun"}]),
        ]

        module_text = """
        This is a longer text with enough content for vocabulary extraction.
        We need at least 50 characters to proceed with the analysis.
        """

        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text=module_text,
            book_id="book-123",
        )

        assert result.success is True
        assert len(result.words) == 1

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_llm_error(self, service, mock_llm_service):
        """Test handling of LLM provider error."""
        from app.services.llm import LLMProviderError

        mock_llm_service.simple_completion.side_effect = LLMProviderError("API Error", "deepseek")

        module_text = """
        This is a longer text with enough content for vocabulary extraction.
        We need at least 50 characters to proceed with the analysis.
        """

        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text=module_text,
            book_id="book-123",
        )

        assert result.success is False
        assert "LLM provider error" in result.error_message

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_filters_short_words(self, service, mock_llm_service):
        """Test that short words are filtered out."""
        llm_response = json.dumps(
            [
                {"word": "a", "translation": "bir", "level": "A1", "part_of_speech": "article"},
                {"word": "is", "translation": "dir", "level": "A1", "part_of_speech": "verb"},
                {"word": "beautiful", "translation": "güzel", "level": "A2", "part_of_speech": "adjective"},
            ]
        )
        mock_llm_service.simple_completion.return_value = llm_response

        module_text = """
        This is a longer text with enough content for vocabulary extraction.
        We need at least 50 characters to proceed with the analysis.
        """

        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text=module_text,
            book_id="book-123",
        )

        assert result.success is True
        # Only "beautiful" should pass the min_word_length filter (default 3)
        assert len(result.words) == 1
        assert result.words[0].word == "beautiful"

    @pytest.mark.asyncio
    async def test_extract_module_vocabulary_validates_level(self, service, mock_llm_service):
        """Test that invalid CEFR levels are cleared."""
        llm_response = json.dumps(
            [
                {"word": "xyzabc", "translation": "test", "level": "INVALID", "part_of_speech": "noun"},
            ]
        )
        mock_llm_service.simple_completion.return_value = llm_response

        module_text = """
        This is a longer text with enough content for vocabulary extraction.
        We need at least 50 characters to proceed with the analysis.
        """

        result = await service.extract_module_vocabulary(
            module_id=1,
            module_title="Unit 1",
            module_text=module_text,
            book_id="book-123",
        )

        assert result.success is True
        # cefrpy doesn't know "xyzabc", so code falls back to LLM level which is invalid and gets cleared
        assert result.words[0].level == ""

    @pytest.mark.asyncio
    async def test_extract_book_vocabulary_success(self, service, mock_llm_service):
        """Test successful vocabulary extraction from a book."""
        llm_response = json.dumps(
            [
                {"word": "hello", "translation": "merhaba", "level": "A1", "part_of_speech": "interjection"},
            ]
        )
        mock_llm_service.simple_completion.return_value = llm_response

        modules = [
            {
                "module_id": 1,
                "title": "Unit 1",
                "text": """
                This is a longer text with enough content for vocabulary extraction.
                We need at least 50 characters to proceed with the analysis.
                Hello and welcome to our lesson.
                """,
                "difficulty": "A1",
            },
            {
                "module_id": 2,
                "title": "Unit 2",
                "text": """
                Another module with sufficient text content for extraction.
                We continue learning new vocabulary words here.
                """,
                "difficulty": "A2",
            },
        ]

        result = await service.extract_book_vocabulary(
            book_id="book-123",
            publisher_slug="pub-456",
            book_name="Test Book",
            modules=modules,
        )

        assert result.book_id == "book-123"
        assert len(result.module_results) == 2
        assert result.success_count == 2

    @pytest.mark.asyncio
    async def test_extract_book_vocabulary_no_modules(self, service):
        """Test handling of empty modules list."""
        with pytest.raises(NoModulesFoundError):
            await service.extract_book_vocabulary(
                book_id="book-123",
                publisher_slug="pub-456",
                book_name="Test Book",
                modules=[],
            )

    @pytest.mark.asyncio
    async def test_extract_book_vocabulary_with_progress(self, service, mock_llm_service):
        """Test vocabulary extraction with progress callback."""
        llm_response = json.dumps(
            [
                {"word": "test", "translation": "test", "level": "A1", "part_of_speech": "noun"},
            ]
        )
        mock_llm_service.simple_completion.return_value = llm_response

        progress_calls = []

        def progress_callback(current: int, total: int) -> None:
            progress_calls.append((current, total))

        modules = [
            {"module_id": 1, "title": "Unit 1", "text": "x" * 100, "difficulty": "A1"},
            {"module_id": 2, "title": "Unit 2", "text": "x" * 100, "difficulty": "A2"},
        ]

        await service.extract_book_vocabulary(
            book_id="book-123",
            publisher_slug="pub-456",
            book_name="Test Book",
            modules=modules,
            progress_callback=progress_callback,
        )

        assert len(progress_calls) == 2
        assert progress_calls[0] == (1, 2)
        assert progress_calls[1] == (2, 2)

    def test_deduplicate_vocabulary(self, service):
        """Test deduplication of vocabulary words."""
        words = [
            VocabularyWord(word="hello", module_id=1),
            VocabularyWord(word="Hello", module_id=2),  # Same word, different case
            VocabularyWord(word="goodbye", module_id=1),
            VocabularyWord(word="hello", module_id=3),  # Duplicate
        ]

        deduplicated = service._deduplicate_vocabulary(words)

        assert len(deduplicated) == 2  # hello and goodbye
        # First occurrence should be kept
        hello_word = next(w for w in deduplicated if w.word.lower() == "hello")
        assert hello_word.module_id == 1

    def test_deduplicate_vocabulary_empty_list(self, service):
        """Test deduplication of empty list."""
        deduplicated = service._deduplicate_vocabulary([])
        assert deduplicated == []


# =============================================================================
# Test Vocabulary Storage
# =============================================================================


class TestVocabularyStorage:
    """Tests for VocabularyStorage."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.minio_publishers_bucket = "publishers"
        return settings

    @pytest.fixture
    def storage(self, mock_settings):
        """Create storage with mocked settings."""
        return VocabularyStorage(settings=mock_settings)

    def test_build_vocabulary_path(self, storage):
        """Test building vocabulary.json path."""
        path = storage._build_vocabulary_path(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
        )
        # Note: book_id is not in the path, only publisher_id and book_name
        assert path == "pub-123/books/Test Book/ai-data/vocabulary.json"

    def test_build_module_path(self, storage):
        """Test building module JSON path."""
        path = storage._build_module_path(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
            module_id=1,
        )
        # Note: book_id is not in the path, only publisher_id and book_name
        assert path == "pub-123/books/Test Book/ai-data/modules/module_1.json"

    def test_build_metadata_path(self, storage):
        """Test building metadata path."""
        path = storage._build_metadata_path(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
        )
        # Note: book_id is not in the path, only publisher_id and book_name
        assert path == "pub-123/books/Test Book/ai-data/vocabulary_metadata.json"

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_load_vocabulary_not_found(self, mock_get_client, storage):
        """Test loading vocabulary when file doesn't exist."""
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_object.side_effect = S3Error(code="NoSuchKey", message="Not found", resource="", request_id="", host_id="", response=None)

        result = storage.load_vocabulary(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
        )

        assert result is None

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_load_vocabulary_success(self, mock_get_client, storage):
        """Test loading vocabulary successfully."""
        vocabulary_data = {
            "language": "en",
            "total_words": 1,
            "words": [{"word": "test"}],
        }

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(vocabulary_data).encode("utf-8")
        mock_client.get_object.return_value = mock_response

        result = storage.load_vocabulary(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
        )

        assert result is not None
        assert result["language"] == "en"
        assert result["total_words"] == 1

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_save_vocabulary(self, mock_get_client, storage):
        """Test saving vocabulary.json."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        words = [VocabularyWord(word="test")]
        book_result = BookVocabularyResult(
            book_id="book-123",
            publisher_id="pub-456",
            book_name="Test Book",
            words=words,
        )

        path = storage.save_vocabulary(book_result)

        assert "vocabulary.json" in path
        mock_client.put_object.assert_called_once()

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_get_module_not_found(self, mock_get_client, storage):
        """Test getting module when it doesn't exist."""
        from minio.error import S3Error

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_object.side_effect = S3Error(code="NoSuchKey", message="Not found", resource="", request_id="", host_id="", response=None)

        result = storage.get_module(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
            module_id=1,
        )

        assert result is None

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_update_module_vocabulary_ids(self, mock_get_client, storage):
        """Test updating module with vocabulary IDs."""
        existing_module = {
            "module_id": 1,
            "title": "Unit 1",
            "text": "Test content",
        }

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(existing_module).encode("utf-8")
        mock_client.get_object.return_value = mock_response

        words = [VocabularyWord(word="hello"), VocabularyWord(word="world")]
        module_result = ModuleVocabularyResult(
            module_id=1,
            words=words,
            success=True,
        )

        path = storage.update_module_vocabulary_ids(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
            module_result=module_result,
        )

        assert path is not None
        mock_client.put_object.assert_called_once()

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_list_modules(self, mock_get_client, storage):
        """Test listing modules."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock list_objects to return module files
        mock_obj1 = MagicMock()
        mock_obj1.object_name = "pub/books/book/Test/ai-data/modules/module_1.json"
        mock_obj2 = MagicMock()
        mock_obj2.object_name = "pub/books/book/Test/ai-data/modules/module_2.json"
        mock_client.list_objects.return_value = [mock_obj1, mock_obj2]

        # Mock get_object for each module
        module1 = {"module_id": 1, "title": "Unit 1"}
        module2 = {"module_id": 2, "title": "Unit 2"}

        mock_response1 = MagicMock()
        mock_response1.read.return_value = json.dumps(module1).encode("utf-8")
        mock_response2 = MagicMock()
        mock_response2.read.return_value = json.dumps(module2).encode("utf-8")

        mock_client.get_object.side_effect = [mock_response1, mock_response2]

        modules = storage.list_modules(
            publisher_slug="pub-123",
            book_id="book-456",
            book_name="Test Book",
        )

        assert len(modules) == 2
        assert modules[0]["module_id"] == 1
        assert modules[1]["module_id"] == 2

    @patch("app.services.vocabulary_extraction.storage.get_minio_client")
    def test_save_all(self, mock_get_client, storage):
        """Test saving all vocabulary data."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Mock get_object for module updates
        existing_module = {"module_id": 1, "title": "Unit 1"}
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(existing_module).encode("utf-8")
        mock_client.get_object.return_value = mock_response

        words = [VocabularyWord(word="test")]
        module_results = [
            ModuleVocabularyResult(module_id=1, words=words, success=True),
        ]
        book_result = BookVocabularyResult(
            book_id="book-123",
            publisher_id="pub-456",
            book_name="Test Book",
            words=words,
            module_results=module_results,
        )

        result = storage.save_all(book_result)

        assert "vocabulary" in result
        assert "updated" in result
        assert "metadata" in result


# =============================================================================
# Test PartOfSpeech Enum
# =============================================================================


class TestPartOfSpeechEnum:
    """Tests for PartOfSpeech enum."""

    def test_enum_values(self):
        """Test that all expected parts of speech are defined."""
        assert PartOfSpeech.NOUN.value == "noun"
        assert PartOfSpeech.VERB.value == "verb"
        assert PartOfSpeech.ADJECTIVE.value == "adjective"
        assert PartOfSpeech.ADVERB.value == "adverb"
        assert PartOfSpeech.PRONOUN.value == "pronoun"
        assert PartOfSpeech.PREPOSITION.value == "preposition"
        assert PartOfSpeech.CONJUNCTION.value == "conjunction"
        assert PartOfSpeech.INTERJECTION.value == "interjection"
        assert PartOfSpeech.UNKNOWN.value == "unknown"
