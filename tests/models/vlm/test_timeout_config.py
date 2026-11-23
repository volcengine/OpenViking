# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for VLM HTTP client configuration.

Before this was wired through, ``_build_openai_client_kwargs`` exposed a
``timeout`` parameter (#1208) but callers never passed it, so the default
timeout was always used and end users could not override it via ``ov.conf``.
These tests lock in that the config value flows through to the underlying
OpenAI and LiteLLM clients.
"""

import asyncio
import json
from unittest import mock

import httpx
import pytest
from litellm import ModelResponse
from pydantic import ValidationError

from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError, match="timeout"):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=0)


@pytest.mark.asyncio
async def test_openai_vlm_applies_http_config_and_closes_clients(monkeypatch):
    connections = 0
    payload = b'{"choices":[{"message":{"role":"assistant","content":"ok"}}]}'

    async def respond(reader, writer):
        nonlocal connections
        connections += 1
        try:
            while True:
                headers = await reader.readuntil(b"\r\n\r\n")
                content_length = next(
                    int(line.split(b":", 1)[1])
                    for line in headers.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                await reader.readexactly(content_length)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    + f"Content-Length: {len(payload)}\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
        except asyncio.IncompleteReadError:
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with await asyncio.start_server(respond, "127.0.0.1", 0) as server:
        config = VLMConfig(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-x",
            api_base=f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1",
            timeout=1.0,
            keepalive_expiry=0,
            max_retries=0,
        )
        vlm = config.get_vlm_instance()
        sync_client = vlm.get_client()
        async_client = vlm.get_async_client()
        try:
            assert sync_client.timeout == 1.0
            assert async_client.timeout == 1.0
            assert await asyncio.to_thread(vlm.get_completion, "first") == "ok"
            assert await asyncio.to_thread(vlm.get_completion, "second") == "ok"
            assert await vlm.get_completion_async("first") == "ok"
            assert await vlm.get_completion_async("second") == "ok"
            assert connections == 4
        finally:
            config.close()
            await asyncio.sleep(0)
        assert sync_client.is_closed()
        assert async_client.is_closed()


def test_openai_completion_uses_default_timeout(monkeypatch):
    requests = []

    def send(client, request, **kwargs):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    config = VLMConfig(
        provider="openai",
        model="gpt-4o-mini",
        api_key="test-key",
        api_base="https://provider.example.test/v1",
    )
    try:
        assert config.get_completion("hello") == "ok"
    finally:
        config.close()
    [request] = requests
    assert request.extensions["timeout"]["read"] == 600.0


def test_litellm_completion_uses_configured_timeout(monkeypatch):
    provider = mock.Mock(
        return_value=ModelResponse(choices=[{"message": {"role": "assistant", "content": "ok"}}])
    )
    monkeypatch.setattr("openviking.models.vlm.backends.litellm_vlm.completion", provider)
    config = VLMConfig(
        provider="litellm", model="openai/gpt-4o-mini", api_key="test-key", timeout=90.0
    )
    try:
        assert config.get_completion("hello") == "ok"
    finally:
        config.close()

    provider.assert_called_once()
    assert provider.call_args.kwargs["timeout"] == 90.0


def test_codex_completion_uses_configured_timeout(monkeypatch):
    requests = []

    def send(client, request, **kwargs):
        requests.append(request)
        event = {
            "type": "response.completed",
            "response": {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "timeout ok"}],
                    }
                ]
            },
        }
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Type": "text/event-stream"},
            text=f"event: response.completed\ndata: {json.dumps(event)}\n\n",
        )

    monkeypatch.setattr(httpx.Client, "send", send)
    config = VLMConfig(
        provider="openai-codex",
        model="gpt-5.3-codex",
        api_key="oauth-token",
        api_base="https://provider.example.test/codex",
        timeout=45.0,
    )

    assert config.get_completion("hello") == "timeout ok"

    [request] = requests
    assert request.url.path == "/codex/responses"
    assert request.extensions["timeout"]["read"] == 45.0
