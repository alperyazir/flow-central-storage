"""LLM Service with provider management and automatic fallback."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.services.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMProviderError,
    LLMProviderType,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
)
from app.services.llm.deepseek import DeepSeekProvider
from app.services.llm.gemini import GeminiProvider

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class LLMService:
    """
    LLM Service with automatic fallback between providers.

    Features:
    - Primary and fallback provider configuration
    - Automatic failover on provider errors
    - Configurable retry logic with exponential backoff
    - Usage logging and tracking
    """

    def __init__(
        self,
        settings: Settings | None = None,
        primary_provider: LLMProvider | None = None,
        fallback_provider: LLMProvider | None = None,
    ) -> None:
        """
        Initialize LLM Service.

        Args:
            settings: Application settings. If not provided, will load from environment.
            primary_provider: Override primary provider instance.
            fallback_provider: Override fallback provider instance.
        """
        self.settings = settings or get_settings()
        self._primary_provider = primary_provider
        self._fallback_provider = fallback_provider
        self._providers: dict[str, LLMProvider] = {}

    def _create_provider(self, provider_type: str) -> LLMProvider | None:
        """
        Create a provider instance based on type.

        Args:
            provider_type: Provider type name ("deepseek", "gemini").

        Returns:
            Provider instance or None if not configured.
        """
        if provider_type == LLMProviderType.DEEPSEEK.value:
            if not self.settings.deepseek_api_key:
                logger.warning("[LLMService] DeepSeek API key not configured")
                return None
            return DeepSeekProvider(
                api_key=self.settings.deepseek_api_key,
                default_model=self.settings.llm_default_model,
                timeout=float(self.settings.llm_timeout_seconds),
                max_retries=self.settings.llm_max_retries,
            )
        elif provider_type == LLMProviderType.GEMINI.value:
            if not self.settings.gemini_api_key:
                logger.warning("[LLMService] Gemini API key not configured")
                return None
            return GeminiProvider(
                api_key=self.settings.gemini_api_key,
                default_model=self.settings.llm_gemini_model,
                vision_model=self.settings.llm_gemini_vision_model,
                timeout=float(self.settings.llm_timeout_seconds),
                max_retries=self.settings.llm_max_retries,
            )
        else:
            logger.error(f"[LLMService] Unknown provider type: {provider_type}")
            return None

    def get_provider(self, provider_type: str) -> LLMProvider | None:
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
    def primary_provider(self) -> LLMProvider | None:
        """Get the primary provider instance."""
        if self._primary_provider:
            return self._primary_provider
        return self.get_provider(self.settings.llm_primary_provider)

    @property
    def fallback_provider(self) -> LLMProvider | None:
        """Get the fallback provider instance."""
        if self._fallback_provider:
            return self._fallback_provider
        return self.get_provider(self.settings.llm_fallback_provider)

    async def _execute_with_retry(
        self,
        provider: LLMProvider,
        request: LLMRequest,
        max_retries: int | None = None,
    ) -> LLMResponse:
        """
        Execute request with retry logic.

        Args:
            provider: Provider to use.
            request: LLM request.
            max_retries: Maximum retry attempts.

        Returns:
            LLM response.

        Raises:
            LLMProviderError: If all retries fail.
        """
        retries = max_retries if max_retries is not None else self.settings.llm_max_retries
        last_error: Exception | None = None
        backoff_times = [1, 2, 4, 8, 16]  # Exponential backoff in seconds

        for attempt in range(retries + 1):
            try:
                return await provider.complete(request)
            except LLMRateLimitError as e:
                last_error = e
                # Use retry-after if provided, otherwise use exponential backoff
                wait_time = e.retry_after or backoff_times[min(attempt, len(backoff_times) - 1)]
                logger.warning(
                    f"[LLMService] Rate limit hit on {provider.provider_name}, "
                    f"waiting {wait_time}s (attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    await asyncio.sleep(wait_time)
            except LLMProviderError as e:
                last_error = e
                logger.warning(
                    f"[LLMService] Provider error on {provider.provider_name}: {e} "
                    f"(attempt {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    await asyncio.sleep(backoff_times[min(attempt, len(backoff_times) - 1)])

        # If we get here, all retries failed
        raise last_error or LLMProviderError(
            message="All retries exhausted",
            provider=provider.provider_name,
        )

    async def complete(
        self,
        request: LLMRequest,
        use_fallback: bool = True,
        force_provider: str | None = None,
    ) -> LLMResponse:
        """
        Generate a completion with automatic fallback.

        Args:
            request: The LLM request.
            use_fallback: Whether to use fallback provider on failure.
            force_provider: Force a specific provider (bypass primary/fallback).

        Returns:
            LLM response.

        Raises:
            LLMProviderError: If all providers fail.
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
                logger.info(f"[LLMService] Using primary provider: {primary.provider_name}")
                return await self._execute_with_retry(primary, request)
            except LLMProviderError as e:
                logger.error(f"[LLMService] Primary provider failed: {e}")
                if not use_fallback:
                    raise

        # Try fallback provider
        if use_fallback:
            fallback = self.fallback_provider
            if fallback and (not primary or fallback.provider_name != primary.provider_name):
                try:
                    logger.info(f"[LLMService] Falling back to: {fallback.provider_name}")
                    return await self._execute_with_retry(fallback, request)
                except LLMProviderError as e:
                    logger.error(f"[LLMService] Fallback provider also failed: {e}")
                    raise

        raise ValueError("No LLM providers available. Configure FCS_DEEPSEEK_API_KEY or FCS_GEMINI_API_KEY.")

    async def chat(
        self,
        messages: list[LLMMessage],
        use_fallback: bool = True,
        **kwargs,
    ) -> LLMResponse:
        """
        Convenience method for chat completions.

        Args:
            messages: List of messages in the conversation.
            use_fallback: Whether to use fallback on failure.
            **kwargs: Additional parameters.

        Returns:
            LLM response.
        """
        request = LLMRequest(
            messages=messages,
            model=kwargs.get("model"),
            max_tokens=kwargs.get("max_tokens", self.settings.llm_max_tokens),
            temperature=kwargs.get("temperature", 0.7),
            top_p=kwargs.get("top_p"),
            stop=kwargs.get("stop"),
        )
        return await self.complete(request, use_fallback=use_fallback)

    async def complete_with_vision(
        self,
        prompt: str,
        images: list[bytes],
        use_fallback: bool = True,
        **kwargs,
    ) -> LLMResponse:
        """
        Generate a completion that includes image analysis.

        Uses Gemini provider for vision capabilities. Falls back to text-only
        response if vision is not available.

        Args:
            prompt: Text prompt describing what to do with the images.
            images: List of images as bytes.
            use_fallback: Whether to use fallback on failure.
            **kwargs: Additional parameters.

        Returns:
            LLM response.
        """
        # Vision requires Gemini (or another vision-capable provider)
        gemini = self.get_provider(LLMProviderType.GEMINI.value)
        if gemini:
            try:
                logger.info("[LLMService] Using Gemini for vision request")
                return await gemini.complete_with_vision(prompt, images, **kwargs)
            except Exception as e:
                logger.error(f"[LLMService] Vision request failed: {e}")
                if not use_fallback:
                    raise

        # No vision-capable provider - raise clear error
        raise ValueError("Vision capabilities require Gemini provider. Configure FCS_GEMINI_API_KEY to enable vision.")

    async def simple_completion(
        self,
        prompt: str,
        system_prompt: str | None = None,
        **kwargs,
    ) -> str:
        """
        Simple interface for single-turn completions.

        Args:
            prompt: User prompt.
            system_prompt: Optional system prompt.
            **kwargs: Additional parameters.

        Returns:
            Generated text content.
        """
        request = LLMRequest.from_prompt(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=kwargs.get("max_tokens", self.settings.llm_max_tokens),
            temperature=kwargs.get("temperature", 0.7),
        )
        response = await self.complete(request)
        return response.content


# Singleton instance for convenience
_llm_service: LLMService | None = None


def get_llm_service() -> LLMService:
    """Get or create the global LLM service instance."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
