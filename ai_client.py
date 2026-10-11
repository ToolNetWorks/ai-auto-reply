import asyncio
import logging
import random
import time
from typing import Any, Optional
import httpx

from config import (
    AI_BASE_URL,
    AI_API_KEY,
    AI_MODEL,
    AI_TIMEOUT,
    AI_MAX_TOKENS,
    AI_TEMPERATURE,
    AI_CONCURRENCY_LIMIT,
    AI_MAX_RETRIES,
)

logger = logging.getLogger(__name__)


class AIClientError(Exception):
    """Base exception for AI client."""
    pass


class AIAuthError(AIClientError):
    """Raised on 401/403 authorization failures."""
    pass


class AIRateLimitError(AIClientError):
    """Raised on 429 rate limit errors."""
    pass


class AIServiceUnavailableError(AIClientError):
    """Raised on 5xx server errors."""
    pass


class AIClient:
    def __init__(
        self,
        base_url: str = AI_BASE_URL,
        api_key: str = AI_API_KEY,
        model: str = AI_MODEL,
        timeout: float = AI_TIMEOUT,
        max_tokens: int = AI_MAX_TOKENS,
        temperature: float = AI_TEMPERATURE,
        concurrency_limit: int = AI_CONCURRENCY_LIMIT,
        max_retries: int = AI_MAX_RETRIES,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(concurrency_limit)
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def create_chat_completion(
        self,
        messages: list[dict[str, str]],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> tuple[str, int, int, float]:
        """
        Calls the OpenAI-compatible chat completions endpoint with exponential backoff and jitter.
        Returns: (content, prompt_tokens, completion_tokens, latency_ms)
        """
        if not self.api_key:
            raise AIAuthError("AI_API_KEY is not configured.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature,
            "stream": False,
        }

        async with self._semaphore:
            client = await self._get_client()
            last_err: Optional[Exception] = None

            for attempt in range(1, self.max_retries + 1):
                start_time = time.monotonic()
                try:
                    logger.debug("AI request attempt %d to %s", attempt, self.model)
                    response = await client.post(url, json=payload, headers=headers)
                    latency_ms = (time.monotonic() - start_time) * 1000

                    if response.status_code == 200:
                        data = response.json()
                        choices = data.get("choices", [])
                        if not choices:
                            raise AIClientError("Empty choices received from AI provider")

                        content = choices[0].get("message", {}).get("content", "").strip()
                        usage = data.get("usage", {})
                        prompt_tokens = usage.get("prompt_tokens", 0)
                        completion_tokens = usage.get("completion_tokens", 0)

                        logger.info(
                            "AI completion successful: model=%s, latency=%.1fms, tokens=%d+%d",
                            self.model,
                            latency_ms,
                            prompt_tokens,
                            completion_tokens,
                        )
                        return content, prompt_tokens, completion_tokens, latency_ms

                    # HTTP Error Handlers
                    status = response.status_code
                    if status in (401, 403):
                        logger.error("AI Auth error (status %d)", status)
                        raise AIAuthError(f"AI API authentication error: {status}")

                    if status == 429:
                        logger.warning("AI rate limited (429) on attempt %d", attempt)
                        if attempt == self.max_retries:
                            raise AIRateLimitError("AI rate limit exceeded after retries.")
                        backoff = (2 ** attempt) + random.uniform(0.5, 1.5)
                        await asyncio.sleep(backoff)
                        continue

                    if status in (500, 502, 503, 504):
                        logger.warning(
                            "AI server error (status %d) on attempt %d", status, attempt
                        )
                        if attempt == self.max_retries:
                            raise AIServiceUnavailableError(
                                f"AI service unavailable (status {status}) after retries."
                            )
                        backoff = (2 ** attempt) + random.uniform(0.2, 1.0)
                        await asyncio.sleep(backoff)
                        continue

                    # Other 4xx
                    raise AIClientError(f"AI request failed with status {status}")

                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    latency_ms = (time.monotonic() - start_time) * 1000
                    logger.warning("AI network/timeout error on attempt %d: %s", attempt, type(e).__name__)
                    last_err = e
                    if attempt == self.max_retries:
                        raise AIClientError(f"AI connection failed after retries: {type(e).__name__}") from e
                    backoff = (1.5 ** attempt) + random.uniform(0.2, 0.8)
                    await asyncio.sleep(backoff)

            raise AIClientError(f"AI request failed after {self.max_retries} retries: {last_err}")
