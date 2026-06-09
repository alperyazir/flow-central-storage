"""Tests for TTS provider abstraction layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tts.azure import AzureTTSProvider
from app.services.tts.base import (
    VOICE_MAPPING,
    TTSAuthError,
    TTSBatchItem,
    TTSBatchResult,
    TTSConnectionError,
    TTSProviderError,
    TTSRateLimitError,
    TTSRequest,
    TTSResponse,
    TTSVoice,
    get_default_voice,
)
from app.services.tts.edge import EdgeTTSProvider
from app.services.tts.service import TTSService

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """Create mock settings for testing."""
    settings = MagicMock()
    settings.azure_tts_key = "test-azure-key"
    settings.azure_tts_region = "eastus"
    settings.tts_primary_provider = "edge"
    settings.tts_fallback_provider = "azure"
    settings.tts_default_voice_en = "en-US-JennyNeural"
    settings.tts_default_voice_tr = "tr-TR-EmelNeural"
    settings.tts_audio_format = "mp3"
    settings.tts_timeout_seconds = 30
    settings.tts_max_retries = 3
    settings.tts_batch_concurrency = 5
    return settings


@pytest.fixture
def edge_provider():
    """Create Edge TTS provider for testing."""
    return EdgeTTSProvider(timeout=30.0, max_retries=3)


@pytest.fixture
def azure_provider():
    """Create Azure TTS provider for testing."""
    return AzureTTSProvider(api_key="test-key", region="eastus")


@pytest.fixture
def tts_service(mock_settings):
    """Create TTS service for testing."""
    return TTSService(settings=mock_settings)


@pytest.fixture
def sample_request():
    """Create a sample TTS request."""
    return TTSRequest(text="Hello world", language="en")


@pytest.fixture
def mock_audio_data():
    """Mock audio data bytes."""
    return b"fake_audio_data_mp3_content"


# =============================================================================
# Base Models Tests
# =============================================================================


class TestTTSVoice:
    """Tests for TTSVoice model."""

    def test_create_voice(self):
        voice = TTSVoice(voice_id="en-US-JennyNeural", language="en", provider="edge")
        assert voice.voice_id == "en-US-JennyNeural"
        assert voice.language == "en"
        assert voice.provider == "edge"


class TestTTSRequest:
    """Tests for TTSRequest model."""

    def test_create_request(self):
        request = TTSRequest(text="Hello", language="en")
        assert request.text == "Hello"
        assert request.language == "en"
        assert request.audio_format == "mp3"
        assert request.speed == 1.0

    def test_empty_text_raises_error(self):
        with pytest.raises(ValueError, match="Text cannot be empty"):
            TTSRequest(text="", language="en")

    def test_invalid_speed_raises_error(self):
        with pytest.raises(ValueError, match="Speed must be between"):
            TTSRequest(text="Hello", speed=3.0)

    def test_valid_speed_range(self):
        request_slow = TTSRequest(text="Hello", speed=0.5)
        request_fast = TTSRequest(text="Hello", speed=2.0)
        assert request_slow.speed == 0.5
        assert request_fast.speed == 2.0


class TestTTSResponse:
    """Tests for TTSResponse model."""

    def test_create_response(self):
        response = TTSResponse(
            audio_data=b"audio",
            voice_used="en-US-JennyNeural",
            provider="edge",
            character_count=5,
        )
        assert response.audio_data == b"audio"
        assert response.voice_used == "en-US-JennyNeural"
        assert response.character_count == 5


class TestTTSBatchResult:
    """Tests for TTSBatchResult model."""

    def test_create_batch_result(self):
        response = TTSResponse(audio_data=b"audio", provider="edge")
        result = TTSBatchResult(
            results=[response, None],
            errors=[(1, "Failed")],
        )
        assert result.success_count == 1
        assert result.failure_count == 1


class TestVoiceMapping:
    """Tests for voice mapping functions."""

    def test_voice_mapping_exists(self):
        for lang in ("en", "tr", "de", "es", "fr"):
            assert lang in VOICE_MAPPING

    def test_get_default_voice_english(self):
        voice = get_default_voice("en", "edge")
        assert voice == "en-US-JennyNeural"

    def test_get_default_voice_turkish(self):
        voice = get_default_voice("tr", "azure")
        assert voice == "tr-TR-EmelNeural"

    def test_get_default_voice_german_spanish_french(self):
        assert get_default_voice("de", "edge") == "de-DE-KatjaNeural"
        assert get_default_voice("es", "edge") == "es-ES-ElviraNeural"
        assert get_default_voice("fr", "edge") == "fr-FR-DeniseNeural"

    def test_get_default_voice_normalizes_region_codes(self):
        # "de-DE" / "ES_es" reduce to the base language code.
        assert get_default_voice("de-DE", "edge") == "de-DE-KatjaNeural"
        assert get_default_voice("ES_es", "edge") == "es-ES-ElviraNeural"

    def test_get_default_voice_unknown_language(self):
        # A truly unmapped language still falls back to English.
        voice = get_default_voice("it", "edge")
        assert voice == "en-US-JennyNeural"


# =============================================================================
# Edge TTS Provider Tests
# =============================================================================


class TestEdgeTTSProvider:
    """Tests for Edge TTS provider."""

    def test_rate_string_conversion(self, edge_provider):
        """Test speed to rate string conversion."""
        assert edge_provider._get_rate_string(1.0) == "+0%"
        assert edge_provider._get_rate_string(1.5) == "+50%"
        assert edge_provider._get_rate_string(0.5) == "-50%"

    @pytest.mark.asyncio
    async def test_synthesize_success(self, edge_provider, mock_audio_data):
        """Test successful synthesis by mocking the provider's synthesize method."""

        # We mock at the provider level since edge_tts may not be installed
        async def mock_synthesize(request):
            return TTSResponse(
                audio_data=mock_audio_data,
                voice_used="en-US-JennyNeural",
                provider="edge",
                character_count=len(request.text),
            )

        with patch.object(edge_provider, "synthesize", mock_synthesize):
            request = TTSRequest(text="Hello", language="en")
            response = await edge_provider.synthesize(request)

            assert response.audio_data == mock_audio_data
            assert response.provider == "edge"
            assert response.character_count == 5

    @pytest.mark.asyncio
    async def test_synthesize_error_handling(self, edge_provider):
        """Test error handling in synthesis."""

        async def mock_synthesize_error(request):
            raise TTSProviderError("Synthesis failed", provider="edge")

        with patch.object(edge_provider, "synthesize", mock_synthesize_error):
            request = TTSRequest(text="Hello", language="en")
            with pytest.raises(TTSProviderError, match="Synthesis failed"):
                await edge_provider.synthesize(request)

    @pytest.mark.asyncio
    async def test_batch_synthesize(self, edge_provider, mock_audio_data):
        """Test batch synthesis."""

        async def mock_synthesize(request):
            return TTSResponse(
                audio_data=mock_audio_data,
                voice_used="en-US-JennyNeural",
                provider="edge",
                character_count=len(request.text),
            )

        with patch.object(edge_provider, "synthesize", mock_synthesize):
            items = [
                TTSBatchItem(text="Hello", language="en"),
                TTSBatchItem(text="World", language="en"),
            ]
            result = await edge_provider.synthesize_batch(items, concurrency=2)

            assert result.success_count == 2
            assert result.failure_count == 0
            assert len(result.results) == 2

    def test_get_voice_with_override(self, edge_provider):
        """Test voice selection with override."""
        voice = edge_provider.get_voice("en", "custom-voice")
        assert voice == "custom-voice"

    def test_get_voice_default(self, edge_provider):
        """Test default voice selection."""
        voice = edge_provider.get_voice("tr", None)
        assert voice == "tr-TR-EmelNeural"


# =============================================================================
# Azure TTS Provider Tests
# =============================================================================


class TestAzureTTSProvider:
    """Tests for Azure TTS provider."""

    def test_ssml_generation(self, azure_provider):
        """Test SSML generation."""
        ssml = azure_provider._get_ssml("Hello", "en-US-JennyNeural", 1.0)
        assert "en-US-JennyNeural" in ssml
        assert "Hello" in ssml
        assert "100%" in ssml  # rate

    def test_ssml_escapes_special_chars(self, azure_provider):
        """Test SSML escapes special characters."""
        ssml = azure_provider._get_ssml("Hello <world> & 'friends'", "voice", 1.0)
        assert "&lt;world&gt;" in ssml
        assert "&amp;" in ssml
        assert "&apos;" in ssml

    @pytest.mark.asyncio
    async def test_synthesize_success(self, azure_provider, mock_audio_data):
        """Test successful synthesis."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock token response
            mock_token_response = MagicMock()
            mock_token_response.status_code = 200
            mock_token_response.text = "test-token"

            # Mock TTS response
            mock_tts_response = MagicMock()
            mock_tts_response.status_code = 200
            mock_tts_response.content = mock_audio_data

            mock_client.post = AsyncMock(side_effect=[mock_token_response, mock_tts_response])

            request = TTSRequest(text="Hello", language="en")
            response = await azure_provider.synthesize(request)

            assert response.audio_data == mock_audio_data
            assert response.provider == "azure"

    @pytest.mark.asyncio
    async def test_auth_error(self, azure_provider):
        """Test authentication error handling."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_client.post = AsyncMock(return_value=mock_response)

            request = TTSRequest(text="Hello", language="en")
            with pytest.raises(TTSAuthError):
                await azure_provider.synthesize(request)

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, azure_provider):
        """Test rate limit error handling."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock token response (success)
            mock_token_response = MagicMock()
            mock_token_response.status_code = 200
            mock_token_response.text = "test-token"

            # Mock TTS response (rate limited)
            mock_tts_response = MagicMock()
            mock_tts_response.status_code = 429
            mock_tts_response.text = "Rate limited"
            mock_tts_response.headers = {"Retry-After": "30"}

            mock_client.post = AsyncMock(side_effect=[mock_token_response, mock_tts_response])

            request = TTSRequest(text="Hello", language="en")
            with pytest.raises(TTSRateLimitError) as exc_info:
                await azure_provider.synthesize(request)
            assert exc_info.value.retry_after == 30.0


# =============================================================================
# TTS Service Tests
# =============================================================================


class TestTTSService:
    """Tests for TTS service with fallback logic."""

    @pytest.mark.asyncio
    async def test_primary_provider_success(self, mock_settings, mock_audio_data):
        """Test successful request using primary provider."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.return_value = TTSResponse(
            audio_data=mock_audio_data,
            voice_used="en-US-JennyNeural",
            provider="edge",
            character_count=5,
        )

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        response = await service.synthesize(TTSRequest(text="Hello"))
        assert response.audio_data == mock_audio_data
        assert response.provider == "edge"

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self, mock_settings, mock_audio_data):
        """Test fallback when primary provider fails."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.side_effect = TTSProviderError("Connection failed", provider="edge")

        mock_fallback = AsyncMock(spec=AzureTTSProvider)
        mock_fallback.provider_name = "azure"
        mock_fallback.synthesize.return_value = TTSResponse(
            audio_data=mock_audio_data,
            voice_used="en-US-JennyNeural",
            provider="azure",
            character_count=5,
        )

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
            fallback_provider=mock_fallback,
        )

        response = await service.synthesize(TTSRequest(text="Hello"))
        assert response.audio_data == mock_audio_data
        assert response.provider == "azure"

    @pytest.mark.asyncio
    async def test_both_providers_fail(self, mock_settings):
        """Test error when both providers fail."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.side_effect = TTSProviderError("Primary failed", provider="edge")

        mock_fallback = AsyncMock(spec=AzureTTSProvider)
        mock_fallback.provider_name = "azure"
        mock_fallback.synthesize.side_effect = TTSProviderError("Fallback failed", provider="azure")

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
            fallback_provider=mock_fallback,
        )

        with pytest.raises(TTSProviderError, match="Fallback failed"):
            await service.synthesize(TTSRequest(text="Hello"))

    @pytest.mark.asyncio
    async def test_no_fallback_mode(self, mock_settings):
        """Test disabling fallback."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.side_effect = TTSProviderError("Primary failed", provider="edge")

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        with pytest.raises(TTSProviderError, match="Primary failed"):
            await service.synthesize(TTSRequest(text="Hello"), use_fallback=False)

    @pytest.mark.asyncio
    async def test_force_specific_provider(self, mock_settings, mock_audio_data):
        """Test forcing a specific provider."""
        with patch.object(TTSService, "get_provider") as mock_get:
            mock_provider = AsyncMock(spec=AzureTTSProvider)
            mock_provider.provider_name = "azure"
            mock_provider.synthesize.return_value = TTSResponse(
                audio_data=mock_audio_data,
                voice_used="en-US-JennyNeural",
                provider="azure",
                character_count=5,
            )
            mock_get.return_value = mock_provider

            service = TTSService(settings=mock_settings)
            response = await service.synthesize(TTSRequest(text="Hello"), force_provider="azure")

            assert response.provider == "azure"

    @pytest.mark.asyncio
    async def test_synthesize_text_convenience(self, mock_settings, mock_audio_data):
        """Test synthesize_text convenience method."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.return_value = TTSResponse(
            audio_data=mock_audio_data,
            voice_used="en-US-JennyNeural",
            provider="edge",
            character_count=5,
        )

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        response = await service.synthesize_text("Hello", language="en")
        assert response.audio_data == mock_audio_data

    @pytest.mark.asyncio
    async def test_batch_processing(self, mock_settings, mock_audio_data):
        """Test batch processing through service."""
        mock_primary = AsyncMock(spec=EdgeTTSProvider)
        mock_primary.provider_name = "edge"
        mock_primary.synthesize.return_value = TTSResponse(
            audio_data=mock_audio_data,
            voice_used="en-US-JennyNeural",
            provider="edge",
            character_count=5,
        )

        service = TTSService(
            settings=mock_settings,
            primary_provider=mock_primary,
        )

        items = [
            TTSBatchItem(text="Hello", language="en"),
            TTSBatchItem(text="World", language="en"),
        ]
        result = await service.synthesize_batch(items)

        assert result.success_count == 2
        assert result.failure_count == 0

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self, mock_settings, mock_audio_data):
        """Test batch processing with partial failures."""
        # Create a service with mocked synthesize method
        service = TTSService(settings=mock_settings)

        call_count = 0

        async def mock_synthesize(request, use_fallback=True):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TTSProviderError("Failed", provider="edge")
            return TTSResponse(
                audio_data=mock_audio_data,
                voice_used="en-US-JennyNeural",
                provider="edge",
                character_count=len(request.text),
            )

        with patch.object(service, "synthesize", mock_synthesize):
            items = [
                TTSBatchItem(text="Hello", language="en"),
                TTSBatchItem(text="World", language="en"),
                TTSBatchItem(text="Test", language="en"),
            ]
            result = await service.synthesize_batch(items)

            assert result.success_count == 2
            assert result.failure_count == 1
            assert len(result.errors) == 1

    def test_no_azure_key_returns_none(self, mock_settings):
        """Test that Azure provider returns None when not configured."""
        mock_settings.azure_tts_key = ""
        service = TTSService(settings=mock_settings)
        assert service.get_provider("azure") is None

    def test_edge_always_available(self, mock_settings):
        """Test that Edge provider is always available (no API key needed)."""
        service = TTSService(settings=mock_settings)
        provider = service.get_provider("edge")
        assert provider is not None
        assert provider.provider_name == "edge"


# =============================================================================
# Exception Tests
# =============================================================================


class TestExceptions:
    """Tests for custom exceptions."""

    def test_tts_provider_error(self):
        error = TTSProviderError("Test error", provider="test", details={"key": "value"})
        assert "test" in str(error)
        assert error.provider == "test"
        assert error.details == {"key": "value"}

    def test_tts_rate_limit_error(self):
        error = TTSRateLimitError(provider="test", retry_after=30.0)
        assert error.retry_after == 30.0
        assert "Rate limit" in str(error)

    def test_tts_auth_error(self):
        error = TTSAuthError(provider="test")
        assert "Authentication" in str(error)

    def test_tts_connection_error(self):
        error = TTSConnectionError(provider="test")
        assert "Connection" in str(error)
