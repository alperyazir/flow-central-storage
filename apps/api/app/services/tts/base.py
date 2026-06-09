"""TTS Provider base interface, models, and exceptions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TTSProviderType(str, Enum):
    """Supported TTS provider types."""

    EDGE = "edge"
    AZURE = "azure"


# =============================================================================
# Voice Mapping
# =============================================================================

# Default voice mapping per language for each provider. Voice IDs are valid for
# both Edge TTS and Azure Speech. Add a new language here to support it.
VOICE_MAPPING: dict[str, dict[str, str]] = {
    "en": {
        "edge": "en-US-JennyNeural",
        "azure": "en-US-JennyNeural",
    },
    "tr": {
        "edge": "tr-TR-EmelNeural",
        "azure": "tr-TR-EmelNeural",
    },
    "de": {
        "edge": "de-DE-KatjaNeural",
        "azure": "de-DE-KatjaNeural",
    },
    "es": {
        "edge": "es-ES-ElviraNeural",
        "azure": "es-ES-ElviraNeural",
    },
    "fr": {
        "edge": "fr-FR-DeniseNeural",
        "azure": "fr-FR-DeniseNeural",
    },
}

# Alternative voices for variety
ALTERNATIVE_VOICES: dict[str, list[str]] = {
    "en": ["en-GB-SoniaNeural", "en-US-GuyNeural"],
    "tr": ["tr-TR-AhmetNeural"],
    "de": ["de-DE-ConradNeural", "de-AT-IngridNeural"],
    "es": ["es-ES-AlvaroNeural", "es-MX-DaliaNeural"],
    "fr": ["fr-FR-HenriNeural", "fr-CA-SylvieNeural"],
}


def normalize_language(language: str | None) -> str:
    """Reduce a language value to a short lookup code (e.g. ``de-DE`` -> ``de``)."""
    if not language:
        return "en"
    return language.strip().lower().replace("_", "-").split("-")[0]


def get_default_voice(language: str, provider: str) -> str:
    """
    Get the default voice for a language and provider.

    Args:
        language: Language code (e.g., "en", "tr", "de", "es", "fr")
        provider: Provider name (e.g., "edge", "azure")

    Returns:
        Voice ID string (e.g., "en-US-JennyNeural"). Falls back to English when
        the language has no mapping.
    """
    lang = normalize_language(language)
    lang_voices = VOICE_MAPPING.get(lang, VOICE_MAPPING.get("en", {}))
    return lang_voices.get(provider, lang_voices.get("edge", "en-US-JennyNeural"))


# =============================================================================
# Exceptions
# =============================================================================


class TTSProviderError(Exception):
    """Base exception for TTS provider errors."""

    def __init__(self, message: str, provider: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.provider = provider
        self.details = details or {}
        super().__init__(f"[{provider}] {message}")


class TTSRateLimitError(TTSProviderError):
    """Raised when provider rate limit is exceeded."""

    def __init__(
        self,
        provider: str,
        retry_after: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after = retry_after
        super().__init__(
            f"Rate limit exceeded. Retry after {retry_after}s" if retry_after else "Rate limit exceeded",
            provider,
            details,
        )


class TTSAuthError(TTSProviderError):
    """Raised when provider authentication fails."""

    def __init__(self, provider: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("Authentication failed - check API key", provider, details)


class TTSConnectionError(TTSProviderError):
    """Raised when connection to provider fails."""

    def __init__(self, provider: str, details: dict[str, Any] | None = None) -> None:
        super().__init__("Connection to provider failed", provider, details)


class TTSVoiceNotFoundError(TTSProviderError):
    """Raised when requested voice is not available."""

    def __init__(self, provider: str, voice: str, details: dict[str, Any] | None = None) -> None:
        self.voice = voice
        super().__init__(f"Voice '{voice}' not found or not available", provider, details)


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class TTSVoice:
    """Represents a TTS voice."""

    voice_id: str  # e.g., "en-US-JennyNeural"
    language: str  # e.g., "en"
    provider: str  # e.g., "edge" or "azure"


@dataclass
class TTSRequest:
    """Request to a TTS provider."""

    text: str
    voice: str | None = None  # If None, use default for language
    language: str = "en"
    audio_format: str = "mp3"
    speed: float = 1.0  # Speech rate multiplier (0.5 to 2.0)

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("Text cannot be empty")
        if self.speed < 0.5 or self.speed > 2.0:
            raise ValueError(f"Speed must be between 0.5 and 2.0, got {self.speed}")


@dataclass
class TTSResponse:
    """Response from a TTS provider."""

    audio_data: bytes  # Raw audio bytes
    duration_ms: int | None = None  # Audio duration if available
    voice_used: str = ""
    provider: str = ""
    character_count: int = 0


@dataclass
class TTSBatchItem:
    """A single item in a batch TTS request."""

    text: str
    voice: str | None = None
    language: str = "en"
    id: str | None = None  # Optional identifier for tracking


@dataclass
class TTSBatchResult:
    """Result of a batch TTS operation."""

    results: list[TTSResponse | None] = field(default_factory=list)  # None for failed items
    errors: list[tuple[int, str]] = field(default_factory=list)  # (index, error_message)
    success_count: int = 0
    failure_count: int = 0

    def __post_init__(self) -> None:
        if not self.success_count and not self.failure_count:
            self.success_count = sum(1 for r in self.results if r is not None)
            self.failure_count = len(self.errors)


# =============================================================================
# Provider Protocol
# =============================================================================


class TTSProvider(ABC):
    """Abstract base class for TTS providers."""

    provider_name: str

    @abstractmethod
    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        """
        Synthesize speech from text.

        Args:
            request: The TTS request containing text and parameters.

        Returns:
            TTSResponse with the audio data and metadata.

        Raises:
            TTSProviderError: If the request fails.
            TTSRateLimitError: If rate limit is exceeded.
            TTSAuthError: If authentication fails.
        """
        ...

    @abstractmethod
    async def synthesize_batch(self, items: list[TTSBatchItem], concurrency: int = 5) -> TTSBatchResult:
        """
        Synthesize speech for multiple items.

        Args:
            items: List of batch items to synthesize.
            concurrency: Maximum concurrent requests.

        Returns:
            TTSBatchResult with results and errors.
        """
        ...

    def get_voice(self, language: str, voice_override: str | None = None) -> str:
        """
        Get the voice to use for a request.

        Args:
            language: Language code.
            voice_override: Optional voice override.

        Returns:
            Voice ID to use.
        """
        if voice_override:
            return voice_override
        return get_default_voice(language, self.provider_name)

    async def health_check(self) -> bool:
        """
        Check if the provider is available and configured correctly.

        Returns:
            True if provider is healthy, False otherwise.
        """
        try:
            response = await self.synthesize(TTSRequest(text="test", language="en"))
            return bool(response.audio_data)
        except Exception:
            return False
