"""Credential spelling variants must not escape model failure diagnostics."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.providers.vlm_adapter import VLMProviderAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        "accessToken",
        "refreshToken",
        "clientSecret",
        "providerApiKey",
        "sessionToken",
        "providerAPIKey",
        "api_key",
    ],
)
@pytest.mark.parametrize("nested", [False, True])
async def test_chat_error_log_redacts_credential_key_variants(monkeypatch, key, nested):
    secret = "synthetic-credential-value"
    error = RuntimeError("synthetic provider failure")
    error.body = {key: secret, "request_count": 7}
    if nested:
        error.body = {"details": [error.body]}
    model = SimpleNamespace(
        provider="openai",
        model="synthetic",
        thinking=False,
        get_completion_async=AsyncMock(side_effect=error),
    )
    messages = []
    monkeypatch.setattr(
        "vikingbot.providers.vlm_adapter.logger.error",
        lambda fmt, *args: messages.append(fmt.format(*args)),
    )
    adapter = VLMProviderAdapter(model, "synthetic", langfuse_client=SimpleNamespace(enabled=False))

    result = await adapter.chat(messages=[{"role": "user", "content": "hello"}])

    assert result.finish_reason == "error"
    assert len(messages) == 1
    assert secret not in messages[0]
    assert "<redacted>" in messages[0]
    assert "request_count" in messages[0]
    assert "7" in messages[0]
