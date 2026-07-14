# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Focused dynamic extra-header coverage for VLM backends."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openviking.models.vlm.backends import litellm_vlm
from openviking.models.vlm.backends.codex_responses_adapter import CodexCompletionsAdapter
from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking.models.vlm.backends.volcengine_vlm import (
    VOLCENGINE_CLIENT_REQUEST_ID_HEADER,
    VolcEngineVLM,
)
from openviking.utils.request_headers import bind_request_headers

HEADERS = {
    "X-Static": "fixed",
    "Authorization": "@request.header.Authorization",
}


def _completion_response(text: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, tool_calls=None),
                finish_reason="stop",
            )
        ],
        usage=None,
    )


async def test_openai_keeps_static_defaults_and_resolves_dynamic_header_per_call() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion_response())

    with patch(
        "openviking.models.vlm.backends.openai_vlm.openai.AsyncOpenAI", return_value=client
    ) as constructor:
        vlm = OpenAIVLM(
            {
                "provider": "openai",
                "api_key": "fallback-key",
                "api_base": "https://example.test/v1",
                "model": "gpt-test",
                "extra_headers": HEADERS,
            }
        )
        assert vlm.get_async_client() is client

    assert constructor.call_args.kwargs["default_headers"] == {"X-Static": "fixed"}
    assert client is vlm.get_async_client()
    with bind_request_headers({"Authorization": "Bearer request-token"}):
        assert await vlm.get_completion_async("hello") == "ok"

    assert client.chat.completions.create.call_args.kwargs["extra_headers"] == {
        "Authorization": "Bearer request-token"
    }


async def test_litellm_resolves_headers_without_logging_secrets(monkeypatch) -> None:
    completion = AsyncMock(return_value=_completion_response())
    trace_info = MagicMock()
    monkeypatch.setattr(litellm_vlm, "acompletion", completion)
    monkeypatch.setattr(litellm_vlm.tracer, "info", trace_info)
    vlm = LiteLLMVLMProvider(
        {
            "provider": "litellm",
            "api_key": "fallback-secret",
            "model": "openai/gpt-test",
            "extra_headers": HEADERS,
        }
    )

    with bind_request_headers({"Authorization": "Bearer request-secret"}):
        assert await vlm.get_completion_async("hello") == "ok"

    assert completion.call_args.kwargs["extra_headers"] == {
        "X-Static": "fixed",
        "Authorization": "Bearer request-secret",
    }
    trace_output = str(trace_info.call_args_list)
    assert "fallback-secret" not in trace_output
    assert "request-secret" not in trace_output


async def test_volcengine_resolves_headers_for_each_request() -> None:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_completion_response())
    vlm = VolcEngineVLM(
        {
            "api_key": "fallback-key",
            "model": "doubao-test",
            "extra_headers": HEADERS,
        }
    )
    vlm.get_async_client = lambda: client

    with bind_request_headers({"Authorization": "Bearer request-token"}):
        assert await vlm.get_completion_async("hello") == "ok"

    resolved = client.chat.completions.create.call_args.kwargs["extra_headers"]
    assert resolved["Authorization"] == "Bearer request-token"
    assert resolved["X-Static"] == "fixed"
    assert VOLCENGINE_CLIENT_REQUEST_ID_HEADER in resolved


def test_codex_adapter_forwards_extra_headers_to_responses_api() -> None:
    completed = SimpleNamespace(output=[], usage=None)
    stream = MagicMock()
    stream.__iter__.return_value = iter(
        [SimpleNamespace(type="response.completed", response=completed)]
    )
    client = MagicMock()
    client.responses.create.return_value = stream
    adapter = CodexCompletionsAdapter(lambda: client, "gpt-test")

    adapter._create_response(
        messages=[{"role": "user", "content": "hello"}],
        extra_headers={"Authorization": "Bearer request-token"},
    )

    assert client.responses.create.call_args.kwargs["extra_headers"] == {
        "Authorization": "Bearer request-token"
    }
