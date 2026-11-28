# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""VLM calls return complete responses, including explicit non-streaming requests."""

import asyncio
import json
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from openviking_cli.utils.config.vlm_config import VLMConfig


@pytest.mark.parametrize(
    "config",
    [
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "stream": False,
        },
        {
            "model": "test-model",
            "providers": {"openai": {"api_key": "test-key", "stream": True}},
        },
        {
            "model": "test-model",
            "credentials": [
                {
                    "provider": "openai",
                    "model": "test-model",
                    "api_key": "test-key",
                    "stream": True,
                }
            ],
        },
        {
            "provider": "openai",
            "model": "test-model",
            "api_key": "test-key",
            "extra_request_body": {"stream": True},
        },
        {
            "model": "test-model",
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "extra_request_body": {"stream": True},
                }
            },
        },
    ],
)
def test_vlm_config_rejects_stream(config):
    with pytest.raises(ValidationError, match="stream.*not supported"):
        VLMConfig(**config)


@pytest.mark.parametrize("stream", [True, None, 0, 1, "false", "true"])
def test_vlm_config_rejects_streaming_and_invalid_body_values(stream):
    with pytest.raises(ValidationError, match="stream.*not supported"):
        VLMConfig(
            provider="openai",
            model="test-model",
            api_key="test-key",
            extra_request_body={"stream": stream},
        )


@pytest.mark.parametrize("provider", ["volcengine", "litellm", "openai-codex"])
def test_vlm_config_rejects_nonstream_body_for_other_backends(provider):
    with pytest.raises(ValidationError, match="stream"):
        VLMConfig(
            provider=provider,
            model="test-model",
            api_key="test-key",
            extra_request_body={"stream": False},
        )


@pytest.mark.parametrize("scope", ["top", "providers", "credentials"])
@pytest.mark.parametrize("explicit_nonstream", [False, True])
@pytest.mark.parametrize(
    "method",
    [
        "get_completion",
        "get_completion_async",
        "get_vision_completion",
        "get_vision_completion_async",
    ],
)
def test_vlm_nonstream_request(monkeypatch, scope, explicit_nonstream, method):
    settings: dict[str, Any] = {"api_key": "test-key", "api_base": "https://gateway.example/v1"}
    if explicit_nonstream:
        settings["extra_request_body"] = {"stream": False}
    if scope == "top":
        config_data = {"provider": "openai", "model": "test-model", **settings}
    elif scope == "providers":
        config_data = {"model": "test-model", "providers": {"openai": settings}}
    else:
        config_data = {"provider": "openai", "model": "test-model", "credentials": [settings]}
    config = VLMConfig.model_validate(config_data)

    requests: list[dict[str, Any]] = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "complete"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def respond_async(transport, request):
        return respond(request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda _, request: respond(request))
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", respond_async)
    try:
        result = getattr(config, method)("hello")
        if method.endswith("_async"):
            result = asyncio.run(result)
        assert result == "complete"
        assert len(requests) == 1
        if explicit_nonstream:
            assert requests[0]["stream"] is False
        else:
            assert "stream" not in requests[0]
    finally:
        config.close()
