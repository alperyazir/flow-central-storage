"""Tests for LLM provider abstraction layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.base import (
    LLMAuthError,
    LLMConnectionError,
    LLMMessage,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from app.services.llm.deepseek import DeepSeekProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.service import LLMService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    settings = MagicMock()
    settings.deepseek_api_key = "test-deepseek-key"
    settings.gemini_api_key = "test-gemini-key"
    settings.llm_primary_provider = "deepseek"
    settings.llm_fallback_provider = "gemini"
    settings.llm_default_model = "deepseek-chat"
    settings.llm_max_tokens = 4096
    settings.llm_timeout_seconds = 60
    settings.llm_max_retries = 3
    return settings


@pytest.fixture
def deepseek_provider():
    """Create DeepSeek provider for testing."""
    return DeepSeekProvider(api_key="test-key")


@pytest.fixture
def gemini_provider():
    """Create Gemini provider for testing."""
    return GeminiProvider(api_key="test-key")


@pytest.fixture
def llm_service(mock_settings):
    """Create LLM service for testing."""
    return LLMService(settings=mock_settings)


@pytest.fixture
def sample_request():
    """Create a sample LLM request."""
    return LLMRequest.from_prompt("Hello, world!")


@pytest.fixture
def mock_deepseek_response():
    """Mock DeepSeek API response."""
    return {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "model": "deepseek-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you?"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
    }


@pytest.fixture
def mock_gemini_response():
    """Mock Gemini API response."""
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello! I'm Gemini."}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 5,
            "candidatesTokenCount": 6,
            "totalTokenCount": 11,
        },
    }


# =============================================================================
# Base Models Tests
# =============================================================================


class TestLLMMessage:
    """Tests for LLMMessage model."""

    def test_create_user_message(self):
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert msg.images is None

    def test_create_system_message(self):
        msg = LLMMessage(role="system", content="You are helpful")
        assert msg.role == "system"

    def test_create_assistant_message(self):
        msg = LLMMessage(role="assistant", content="Hi there")
        assert msg.role == "assistant"

    def test_invalid_role_raises_error(self):
        with pytest.raises(ValueError, match="Invalid role"):
            LLMMessage(role="invalid", content="test")

    def test_message_with_images(self):
        msg = LLMMessage(role="user", content="Describe this", images=[b"fake_image"])
        assert msg.images == [b"fake_image"]


class TestLLMRequest:
    """Tests for LLMRequest model."""

    def test_create_from_prompt(self):
        request = LLMRequest.from_prompt("Hello")
        assert len(request.messages) == 1
        assert request.messages[0].role == "user"
        assert request.messages[0].content == "Hello"

    def test_create_from_prompt_with_system(self):
        request = LLMRequest.from_prompt("Hello", system_prompt="Be helpful")
        assert len(request.messages) == 2
        assert request.messages[0].role == "system"
        assert request.messages[1].role == "user"

    def test_default_temperature(self):
        request = LLMRequest(messages=[LLMMessage(role="user", content="test")])
        assert request.temperature == 0.7


class TestLLMUsage:
    """Tests for LLMUsage model."""

    def test_total_tokens_calculated(self):
        usage = LLMUsage(prompt_tokens=10, completion_tokens=5)
        assert usage.total_tokens == 15

    def test_cost_estimation(self):
        usage = LLMUsage(prompt_tokens=100, completion_tokens=50, estimated_cost_usd=0.001)
        assert usage.estimated_cost_usd == 0.001


# =============================================================================
# DeepSeek Provider Tests
# =============================================================================


class TestDeepSeekProvider:
    """Tests for DeepSeek provider."""

    @pytest.mark.asyncio
    async def test_complete_success(self, deepseek_provider, mock_deepseek_response):
        """Test successful completion."""
        with patch.object(deepseek_provider, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_deepseek_response

            request = LLMRequest.from_prompt("Hello")
            response = await deepseek_provider.complete(request)

            assert response.content == "Hello! How can I help you?"
            assert response.provider == "deepseek"
            assert response.usage.prompt_tokens == 10
            assert response.usage.completion_tokens == 8

    @pytest.mark.asyncio
    async def test_chat_convenience_method(self, deepseek_provider, mock_deepseek_response):
        """Test chat convenience method."""
        with patch.object(deepseek_provider, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_deepseek_response

            messages = [LLMMessage(role="user", content="Hi")]
            response = await deepseek_provider.chat(messages)

            assert response.content == "Hello! How can I help you?"

    @pytest.mark.asyncio
    async def test_response_without_choices_is_a_provider_error(self, deepseek_provider):
        """A 200 with no choices must be retryable, not KeyError 'choices'."""
        for body in ({}, {"choices": []}, {"error": {"message": "busy"}}, {"choices": [{}]}):
            with patch.object(deepseek_provider, "_make_request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = body
                with pytest.raises(LLMProviderError, match="no choices"):
                    await deepseek_provider.complete(LLMRequest.from_prompt("Hello"))

    @pytest.mark.asyncio
    async def test_vision_not_supported(self, deepseek_provider):
        """Test that DeepSeek raises NotImplementedError for vision."""
        with pytest.raises(NotImplementedError, match="does not support vision"):
            await deepseek_provider.complete_with_vision("describe", [b"image"])

    def test_cost_estimation(self, deepseek_provider):
        """Test cost estimation for DeepSeek."""
        usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = deepseek_provider.estimate_cost(usage, "deepseek-chat")
        # Input: 1M * $0.14/M = $0.14
        # Output: 1M * $0.28/M = $0.28
        # Total: $0.42
        assert cost == pytest.approx(0.42, rel=0.01)

    @pytest.mark.asyncio
    async def test_auth_error(self, deepseek_provider):
        """Test authentication error handling."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_post.return_value = mock_response

            with pytest.raises(LLMAuthError):
                await deepseek_provider.complete(LLMRequest.from_prompt("test"))

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, deepseek_provider):
        """Test rate limit error handling."""
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.text = "Rate limited"
            mock_response.headers = {"Retry-After": "30"}
            mock_post.return_value = mock_response

            with pytest.raises(LLMRateLimitError) as exc_info:
                await deepseek_provider.complete(LLMRequest.from_prompt("test"))
            assert exc_info.value.retry_after == 30.0


# =============================================================================
# Gemini Provider Tests
# =============================================================================


class TestGeminiProvider:
    """Tests for Gemini provider."""

    @pytest.mark.asyncio
    async def test_complete_success(self, gemini_provider, mock_gemini_response):
        """Test successful completion."""
        with patch.object(gemini_provider, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_gemini_response

            request = LLMRequest.from_prompt("Hello")
            response = await gemini_provider.complete(request)

            assert response.content == "Hello! I'm Gemini."
            assert response.provider == "gemini"
            assert response.usage.prompt_tokens == 5
            assert response.usage.completion_tokens == 6

    @pytest.mark.asyncio
    async def test_vision_support(self, gemini_provider, mock_gemini_response):
        """Test vision completion."""
        with patch.object(gemini_provider, "_make_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_gemini_response

            # PNG magic bytes
            fake_image = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

            response = await gemini_provider.complete_with_vision("Describe this image", [fake_image])

            assert response.content == "Hello! I'm Gemini."
            # Verify the request was made with the vision model
            mock_request.assert_called_once()

    def test_image_type_detection(self, gemini_provider):
        """Test MIME type detection from magic bytes."""
        # PNG
        assert gemini_provider._detect_image_type(b"\x89PNG\r\n\x1a\n") == "image/png"
        # JPEG
        assert gemini_provider._detect_image_type(b"\xff\xd8\xff") == "image/jpeg"
        # GIF
        assert gemini_provider._detect_image_type(b"GIF89a") == "image/gif"
        # WebP
        assert gemini_provider._detect_image_type(b"RIFF\x00\x00\x00\x00WEBP") == "image/webp"
        # Unknown (defaults to JPEG)
        assert gemini_provider._detect_image_type(b"unknown") == "image/jpeg"

    def test_cost_estimation(self, gemini_provider):
        """Test cost estimation for Gemini."""
        usage = LLMUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)
        cost = gemini_provider.estimate_cost(usage, "gemini-1.5-flash")
        # Input: 1M * $0.075/M = $0.075
        # Output: 1M * $0.30/M = $0.30
        # Total: $0.375
        assert cost == pytest.approx(0.375, rel=0.01)


# =============================================================================
# LLM Service Tests
# =============================================================================


class TestLLMService:
    """Tests for LLM service with fallback logic."""

    @pytest.mark.asyncio
    async def test_primary_provider_success(self, mock_settings, mock_deepseek_response):
        """Test successful request using primary provider."""
        mock_primary = AsyncMock(spec=DeepSeekProvider)
        mock_primary.provider_name = "deepseek"
        mock_primary.complete.return_value = LLMResponse(
            content="Hello from primary",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            model="deepseek-chat",
            provider="deepseek",
        )

        service = LLMService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        response = await service.complete(LLMRequest.from_prompt("test"))
        assert response.content == "Hello from primary"
        assert response.provider == "deepseek"

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, mock_settings):
        """Test fallback when primary provider fails."""
        mock_primary = AsyncMock(spec=DeepSeekProvider)
        mock_primary.provider_name = "deepseek"
        mock_primary.complete.side_effect = LLMProviderError("API Error", provider="deepseek")

        mock_fallback = AsyncMock(spec=GeminiProvider)
        mock_fallback.provider_name = "gemini"
        mock_fallback.complete.return_value = LLMResponse(
            content="Hello from fallback",
            usage=LLMUsage(prompt_tokens=10, completion_tokens=5),
            model="gemini-1.5-flash",
            provider="gemini",
        )

        service = LLMService(
            settings=mock_settings,
            primary_provider=mock_primary,
            fallback_provider=mock_fallback,
        )

        response = await service.complete(LLMRequest.from_prompt("test"))
        assert response.content == "Hello from fallback"
        assert response.provider == "gemini"

    @pytest.mark.asyncio
    async def test_both_providers_fail(self, mock_settings):
        """Test error when both providers fail."""
        mock_primary = AsyncMock(spec=DeepSeekProvider)
        mock_primary.provider_name = "deepseek"
        mock_primary.complete.side_effect = LLMProviderError("Primary failed", provider="deepseek")

        mock_fallback = AsyncMock(spec=GeminiProvider)
        mock_fallback.provider_name = "gemini"
        mock_fallback.complete.side_effect = LLMProviderError("Fallback failed", provider="gemini")

        service = LLMService(
            settings=mock_settings,
            primary_provider=mock_primary,
            fallback_provider=mock_fallback,
        )

        with pytest.raises(LLMProviderError, match="Fallback failed"):
            await service.complete(LLMRequest.from_prompt("test"))

    @pytest.mark.asyncio
    async def test_no_fallback_mode(self, mock_settings):
        """Test disabling fallback."""
        mock_primary = AsyncMock(spec=DeepSeekProvider)
        mock_primary.provider_name = "deepseek"
        mock_primary.complete.side_effect = LLMProviderError("Primary failed", provider="deepseek")

        service = LLMService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        with pytest.raises(LLMProviderError, match="Primary failed"):
            await service.complete(LLMRequest.from_prompt("test"), use_fallback=False)

    @pytest.mark.asyncio
    async def test_fallback_switched_off_surfaces_primary_error(self, mock_settings):
        """FCS_LLM_FALLBACK_PROVIDER=none: fail with DeepSeek's error, never call Gemini."""
        for value in ("none", "", "None"):
            mock_settings.llm_fallback_provider = value
            mock_primary = AsyncMock(spec=DeepSeekProvider)
            mock_primary.provider_name = "deepseek"
            mock_primary.complete.side_effect = LLMProviderError("Primary failed", provider="deepseek")

            service = LLMService(settings=mock_settings, primary_provider=mock_primary)
            with patch.object(service, "_create_provider") as create, \
                    patch("app.services.llm.service.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(LLMProviderError, match="Primary failed"):
                    await service.complete(LLMRequest.from_prompt("test"))
                create.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_specific_provider(self, mock_settings):
        """Test forcing a specific provider."""
        with patch.object(LLMService, "get_provider") as mock_get:
            mock_provider = AsyncMock(spec=GeminiProvider)
            mock_provider.provider_name = "gemini"
            mock_provider.complete.return_value = LLMResponse(
                content="Forced gemini",
                usage=LLMUsage(prompt_tokens=5, completion_tokens=5),
                model="gemini-1.5-flash",
                provider="gemini",
            )
            mock_get.return_value = mock_provider

            service = LLMService(settings=mock_settings)
            response = await service.complete(LLMRequest.from_prompt("test"), force_provider="gemini")

            assert response.provider == "gemini"

    @pytest.mark.asyncio
    async def test_simple_completion(self, mock_settings):
        """Test simple_completion convenience method."""
        mock_primary = AsyncMock(spec=DeepSeekProvider)
        mock_primary.provider_name = "deepseek"
        mock_primary.complete.return_value = LLMResponse(
            content="Simple response",
            usage=LLMUsage(prompt_tokens=5, completion_tokens=3),
            model="deepseek-chat",
            provider="deepseek",
        )

        service = LLMService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        result = await service.simple_completion("Hello")
        assert result == "Simple response"

    @pytest.mark.asyncio
    async def test_vision_uses_gemini(self, mock_settings):
        """Test that vision requests use Gemini provider."""
        mock_gemini = AsyncMock(spec=GeminiProvider)
        mock_gemini.provider_name = "gemini"
        mock_gemini.complete_with_vision.return_value = LLMResponse(
            content="I see an image",
            usage=LLMUsage(prompt_tokens=50, completion_tokens=10),
            model="gemini-1.5-flash",
            provider="gemini",
        )

        service = LLMService(settings=mock_settings)
        service._providers["gemini"] = mock_gemini

        response = await service.complete_with_vision("Describe", [b"image_data"])
        assert response.content == "I see an image"
        mock_gemini.complete_with_vision.assert_called_once()

    def test_no_providers_configured(self):
        """Test error when no providers are configured."""
        mock_settings = MagicMock()
        mock_settings.deepseek_api_key = ""
        mock_settings.gemini_api_key = ""
        mock_settings.llm_primary_provider = "deepseek"
        mock_settings.llm_fallback_provider = "gemini"

        service = LLMService(settings=mock_settings)
        assert service.primary_provider is None
        assert service.fallback_provider is None


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""

    def test_llm_provider_error(self):
        error = LLMProviderError("Test error", provider="test", details={"key": "value"})
        assert "test" in str(error)
        assert error.provider == "test"
        assert error.details == {"key": "value"}

    def test_llm_rate_limit_error(self):
        error = LLMRateLimitError(provider="test", retry_after=30.0)
        assert error.retry_after == 30.0
        assert "Rate limit" in str(error)

    def test_llm_auth_error(self):
        error = LLMAuthError(provider="test")
        assert "Authentication" in str(error)

    def test_llm_connection_error(self):
        error = LLMConnectionError(provider="test")
        assert "Connection" in str(error)
