"""TTS Service with provider management and automatic fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.services.tts.azure import AzureTTSProvider
from app.services.tts.base import (
    TTSBatchItem,
    TTSBatchResult,
    TTSProvider,
    TTSProviderError,
    TTSProviderType,
    TTSRateLimitError,
    TTSRequest,
    TTSResponse,
)
from app.services.tts.edge import EdgeTTSProvider

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class TTSService:
    """
    TTS Service with automatic fallback between providers.

    Features:
    - Primary and fallback provider configuration
    - Automatic failover on provider errors
    - Configurable retry logic with exponential backoff
    - Batch processing with concurrency control
    """

    def __init__(
        self,
        settings: Settings | None = None,
        primary_provider: TTSProvider | None = None,
        fallback_provider: TTSProvider | None = None,
    ) -> None:
        """
        Initialize TTS Service.

        Args:
            settings: Application settings. If not provided, will load from environment.
            primary_provider: Override primary provider instance.
            fallback_provider: Override fallback provider instance.
        """
        self.settings = settings or get_settings()
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self._providers: dict[str, TTSProvider] = {}

    def _create_provider(self, provider_type: str) -> TTSProvider | None:
        """
        Create a provider instance based on type.

        Args:
            provider_type: Provider type name ("edge", "azure").

        Returns:
            Provider instance or None if not configured.
        """
        if provider_type == TTSProviderType.EDGE.value:
            # Edge TTS doesn't require API key
            return EdgeTTSProvider(
                timeout=float(self.settings.tts_timeout_seconds),
                max_retries=self.settings.tts_max_retries,
            )
        elif provider_type == TTSProviderType.AZURE.value:
            if not self.settings.azure_tts_key:
                logger.warning("[TTSService] Azure TTS API key not configured")
                return None
            return AzureTTSProvider(
                api_key=self.settings.azure_tts_key,
                region=self.settings.azure_tts_region,
                timeout=float(self.settings.tts_timeout_seconds),
                max_retries=self.settings.tts_max_retries,
            )
        else:
            logger.error(f"[TTSService] Unknown provider type: {provider_type}")
            return None

    def get_provider(self, provider_type: str) -> TTSProvider | None:
        """
        Get or create a provider instance.

        Args:
            provider_type: Provider type name.

        Returns:
            Provider instance or None if not available.
        """
        if provider_type not in self._providers:
            provider = self._create_provider(provider_type)
            if provider:
                self._providers[provider_type] = provider
        return self._providers.get(provider_type)

    @property
    def primary_provider(self) -> TTSProvider | None:
        """Get the primary provider instance."""
        if self._primary_provider:
            return self._primary_provider
        return self.get_provider(self.settings.tts_primary_provider)

    @property
    def fallback_provider(self) -> TTSProvider | None:
        """Get the fallback provider instance."""
        if self._fallback_provider:
            return self._fallback_provider
        return self.get_provider(self.settings.tts_fallback_provider)

    async def _execute_with_retry(
        self,
        provider: TTSProvider,
        request: TTSRequest,
        max_retries: int | None = None,
    ) -> TTSResponse:
        """
        Execute request with retry logic.

        Args:
            provider: Provider to use.
            request: TTS request.
            max_retries: Maximum retry attempts.

        Returns:
            TTS response.

        Raises:
            TTSProviderError: If all retries fail.
        """
        retries = max_retries if max_retries is not None else self.settings.tts_max_retries
        last_error: Exception | None = None
        backoff_times = [1, 2, 4, 8, 16]  # Exponential backoff in seconds

        for attempt in range(retries + 1):
            try:
                return await provider.synthesize(request)
            except TTSRateLimitError as e:
                last_error = e
                wait_time = e.retry_after or backoff_times[min(attempt, len(backoff_times) - 1)]
                logger.warning(
                    f"[TTSService] Rate limit hit on {provider.provider_name}, "
                    f"waiting {wait_time}s (attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    await asyncio.sleep(wait_time)
            except TTSProviderError as e:
                last_error = e
                logger.warning(
                    f"[TTSService] Provider error on {provider.provider_name}: {e} "
                    f"(attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    await asyncio.sleep(backoff_times[min(attempt, len(backoff_times) - 1)])

        # If we get here, all retries failed
        raise last_error or TTSProviderError(
            message="All retries exhausted",
            provider=provider.provider_name,
        )

    async def synthesize(
        self,
        request: TTSRequest,
        use_fallback: bool = True,
        force_provider: str | None = None,
    ) -> TTSResponse:
        """
        Synthesize speech with automatic fallback.

        Args:
            request: The TTS request.
            use_fallback: Whether to use fallback provider on failure.
            force_provider: Force a specific provider (bypass primary/fallback).

        Returns:
            TTS response.

        Raises:
            TTSProviderError: If all providers fail.
            ValueError: If no providers are configured.
        """
        # Handle forced provider
        if force_provider:
            provider = self.get_provider(force_provider)
            if not provider:
                raise ValueError(f"Provider '{force_provider}' not configured or unavailable")
            return await self._execute_with_retry(provider, request)

        # Try primary provider
        primary = self.primary_provider
        if primary:
            try:
                logger.info(f"[TTSService] Using primary provider: {primary.provider_name}")
                return await self._execute_with_retry(primary, request)
            except TTSProviderError as e:
                logger.error(f"[TTSService] Primary provider failed: {e}")
                if not use_fallback:
                    raise

        # Try fallback provider
        if use_fallback:
            fallback = self.fallback_provider
            if fallback and (not primary or fallback.provider_name != primary.provider_name):
                try:
                    logger.info(f"[TTSService] Falling back to: {fallback.provider_name}")
                    return await self._execute_with_retry(fallback, request)
                except TTSProviderError as e:
                    logger.error(f"[TTSService] Fallback provider also failed: {e}")
                    raise

        raise ValueError(
            "No TTS providers available. Edge TTS should always be available. "
            "Configure FCS_AZURE_TTS_KEY for Azure fallback."
        )

    async def synthesize_text(
        self,
        text: str,
        language: str = "en",
        voice: str | None = None,
        speed: float = 1.0,
        use_fallback: bool = True,
    ) -> TTSResponse:
        """
        Simple interface for text-to-speech synthesis.

        Args:
            text: Text to synthesize.
            language: Language code.
            voice: Optional voice override.
            speed: Speech rate multiplier.
            use_fallback: Whether to use fallback on failure.

        Returns:
            TTS response.
        """
        request = TTSRequest(
            text=text,
            voice=voice,
            language=language,
            audio_format=self.settings.tts_audio_format,
            speed=speed,
        )
        return await self.synthesize(request, use_fallback=use_fallback)

    async def synthesize_batch(
        self,
        items: list[TTSBatchItem],
        concurrency: int | None = None,
        use_fallback: bool = True,
    ) -> TTSBatchResult:
        """
        Synthesize speech for multiple items with fallback support.

        Args:
            items: List of batch items to synthesize.
            concurrency: Maximum concurrent requests.
            use_fallback: Whether to use fallback on failure.

        Returns:
            TTSBatchResult with results and errors.
        """
        concurrency = concurrency or self.settings.tts_batch_concurrency
        results: list[TTSResponse | None] = [None] * len(items)
        errors: list[tuple[int, str]] = []
        semaphore = asyncio.Semaphore(concurrency)

        async def process_item(index: int, item: TTSBatchItem) -> None:
            async with semaphore:
                try:
                    request = TTSRequest(
                        text=item.text,
                        voice=item.voice,
                        language=item.language,
                        audio_format=self.settings.tts_audio_format,
                    )
                    response = await self.synthesize(request, use_fallback=use_fallback)
                    results[index] = response
                except Exception as e:
                    errors.append((index, str(e)))
                    logger.warning(f"[TTSService] Batch item {index} failed: {e}")

        # Process all items concurrently with semaphore limiting
        tasks = [process_item(i, item) for i, item in enumerate(items)]
        await asyncio.gather(*tasks)

        success_count = sum(1 for r in results if r is not None)
        failure_count = len(errors)

        logger.info(f"[TTSService] Batch complete: {success_count} succeeded, {failure_count} failed")

        return TTSBatchResult(
            results=results,
            errors=errors,
            success_count=success_count,
            failure_count=failure_count,
        )


# Singleton instance for convenience
_tts_service: TTSService | None = None


def get_tts_service() -> TTSService:
    """Get or create the global TTS service instance."""
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
