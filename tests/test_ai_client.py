import asyncio
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from ai_client import AIAuthError, AIClient, AIClientError, AIRateLimitError
from config import ConfigError, validate_config


@pytest.mark.asyncio
async def test_8_api_timeout_handled_gracefully():
    """Test 8: API timeout → không crash."""
    client = AIClient(
        base_url="https://api.toolnet.tech/v1",
        api_key="sk-dummy",
        model="dummy-model",
        timeout=0.1,
        max_retries=2,
    )

    # Mock post to simulate TimeoutException
    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Read timed out")):
        with pytest.raises(AIClientError) as exc_info:
            await client.create_chat_completion([{"role": "user", "content": "hi"}])
        assert "AI connection failed after retries" in str(exc_info.value)
    
    await client.close()


@pytest.mark.asyncio
async def test_9_api_429_retries_with_limit():
    """Test 9: API 429 → retry có giới hạn."""
    client = AIClient(
        base_url="https://api.toolnet.tech/v1",
        api_key="sk-dummy",
        model="dummy-model",
        max_retries=2,
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 429

    call_count = 0

    async def mock_post(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        with pytest.raises(AIRateLimitError):
            await client.create_chat_completion([{"role": "user", "content": "hi"}])

    # Must have retried up to max_retries
    assert call_count == 2
    await client.close()


def test_13_missing_api_key_reports_config_error():
    """Test 13: ToolNet API key thiếu → báo lỗi cấu hình."""
    # When AI_API_KEY is missing
    with patch("config.AI_API_KEY", ""):
        with pytest.raises(ConfigError) as exc_info:
            validate_config(require_ai=True)
        assert "Missing AI_API_KEY" in str(exc_info.value)
