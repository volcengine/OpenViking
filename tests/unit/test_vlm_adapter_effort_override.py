"""Explicit adapter overrides must reach the existing backend request builder."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from vikingbot.providers.vlm_adapter import VLMProviderAdapter

from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure", "litellm"])
@pytest.mark.parametrize("override,expected", [("high", "high"), (None, "low")])
async def test_adapter_effort_override_reaches_backend_request(
    monkeypatch, provider, override, expected
):
    response = NS(
        choices=[
            NS(
                finish_reason="stop",
                message=NS(
                    content="ok",
                    tool_calls=[],
                    reasoning_content=None,
                ),
            )
        ],
        usage=None,
    )
    create = AsyncMock(return_value=response)
    config = {"provider": provider, "model": "synthetic", "reasoning_effort": "low"}
    if provider == "litellm":
        model = LiteLLMVLMProvider(config)
        monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.acompletion", create)
    else:
        model = OpenAIVLM(config)
        monkeypatch.setattr(
            model, "get_async_client", lambda: NS(chat=NS(completions=NS(create=create)))
        )
    adapter = VLMProviderAdapter(model, "synthetic", langfuse_client=NS(enabled=False))

    result = await adapter.chat(
        messages=[{"role": "user", "content": "hello"}], reasoning_effort=override
    )

    assert result.content == "ok"
    create.assert_awaited_once()
    assert create.await_args.kwargs["reasoning_effort"] == expected
