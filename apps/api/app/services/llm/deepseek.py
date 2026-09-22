"""DeepSeek LLM provider implementation."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.llm.base import (
    LLMAuthError,
    LLMConnectionError,
    LLMMessage,
    LLMModelNotFoundError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMUsage,
)

logger = logging.getLogger(__name__)


# DeepSeek pricing per 1M tokens (as of late 2024)
DEEPSEEK_PRICING = {
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-coder": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}


class DeepSeekProvider(LLMProvider):
    """DeepSeek LLM provider using OpenAI-compatible API."""

    provider_name = "deepseek"
    BASE_URL = "https://api.deepseek.com/v1"
    DEFAULT_MODEL = "deepseek-chat"

    def __init__(
        self,
        api_key: str,
        default_model: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        """
        Initialize DeepSeek provider.

        Args:
            api_key: DeepSeek API key.
            default_model: Default model to use if not specified in requests.
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retries for transient errors.
        """
        self.api_key = api_key
        self.default_model = default_model or self.DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries

    def _get_headers(self) -> dict[str, str]:
        """Get HTTP headers for API requests."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _convert_messages(self, messages: list[LLMMessage]) -> list[dict[str, str]]:
        """Convert LLMMessage objects to API format."""
        return [{"role": msg.role, "content": msg.content} for msg in messages]

    async def _make_request(
        self,
        endpoint: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Make an API request to DeepSeek.

        Args:
            endpoint: API endpoint path.
            payload: Request payload.

        Returns:
            API response as dictionary.

        Raises:
            LLMProviderError: If request fails.
        """
        url = f"{self.BASE_URL}/{endpoint}"

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=self._get_headers(),
                )

                if response.status_code == 401:
                    raise LLMAuthError(
                        provider=self.provider_name,
                        details={"status_code": 401, "response": response.text},
                    )

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    raise LLMRateLimitError(
                        provider=self.provider_name,
                        retry_after=float(retry_after) if retry_after else None,
                        details={"response": response.text},
                    )

                if response.status_code == 404:
                    raise LLMModelNotFoundError(
                        provider=self.provider_name,
                        model=payload.get("model", "unknown"),
                        details={"response": response.text},
                    )

                if response.status_code >= 400:
                    raise LLMProviderError(
                        message=f"API error: {response.status_code}",
                        provider=self.provider_name,
                        details={"status_code": response.status_code, "response": response.text},
                    )

                return response.json()

            except httpx.ConnectError as e:
                raise LLMConnectionError(
                    provider=self.provider_name,
                    details={"error": str(e)},
                ) from e
            except httpx.TimeoutException as e:
                raise LLMConnectionError(
                    provider=self.provider_name,
                    details={"error": f"Request timeout: {e}"},
                ) from e

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Generate a completion for the given request.

        Args:
            request: The LLM request containing messages and parameters.

        Returns:
            LLMResponse with the generated content and usage stats.
        """
        model = request.model or self.default_model

        payload: dict[str, Any] = {
            "model": model,
            "messages": self._convert_messages(request.messages),
            "temperature": request.temperature,
        }

        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop:
            payload["stop"] = request.stop

        logger.debug(f"[DeepSeek] Sending request to model {model}")

        response_data = await self._make_request("chat/completions", payload)

        # Parse response. DeepSeek can answer 200 OK with no choices (seen under
        # load on 2026-09-14); make that a provider error so it is retried and
        # the stage fails with a readable message instead of KeyError 'choices'.
        choices = response_data.get("choices") if isinstance(response_data, dict) else None
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise LLMProviderError(
                message="Response had no choices",
                provider=self.provider_name,
                details={"response": str(response_data)[:500]},
            )
        choice = choices[0]
        content = choice["message"].get("content") or ""
        finish_reason = choice.get("finish_reason")

        # Parse usage
        usage_data = response_data.get("usage", {})
        usage = LLMUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
        )

        # Estimate cost
        usage.estimated_cost_usd = self.estimate_cost(usage, model)

        logger.info(
            f"[DeepSeek] Completed request: {usage.total_tokens} tokens, ${usage.estimated_cost_usd:.6f} estimated cost"
        )

        return LLMResponse(
            content=content,
            usage=usage,
            model=model,
            provider=self.provider_name,
            finish_reason=finish_reason,
            raw_response=response_data,
        )

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        """
        Convenience method for chat completions.

        Args:
            messages: List of messages in the conversation.
            **kwargs: Additional parameters (model, max_tokens, temperature, etc.)

        Returns:
            LLMResponse with the generated content and usage stats.
        """
        request = LLMRequest(
            messages=messages,
            model=kwargs.get("model"),
            max_tokens=kwargs.get("max_tokens"),
            temperature=kwargs.get("temperature", 0.7),
            top_p=kwargs.get("top_p"),
            stop=kwargs.get("stop"),
        )
        return await self.complete(request)

    async def complete_with_vision(self, prompt: str, images: list[bytes], **kwargs: Any) -> LLMResponse:
        """
        DeepSeek does not support vision. Raises NotImplementedError.

        For vision tasks, use Gemini provider instead.
        """
        raise NotImplementedError(
            f"{self.provider_name} does not support vision. Use Gemini provider for image analysis."
        )

    def estimate_cost(self, usage: LLMUsage, model: str) -> float:
        """
        Estimate the cost for the given usage.

        Args:
            usage: Token usage information.
            model: The model used.

        Returns:
            Estimated cost in USD.
        """
        pricing = DEEPSEEK_PRICING.get(model, DEEPSEEK_PRICING["deepseek-chat"])
        input_cost = (usage.prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (usage.completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost
