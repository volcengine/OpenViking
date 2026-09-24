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
from unittest import mock

import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import (
    OpenAIVLM,
    _build_openai_client_kwargs,
)
from openviking_cli.utils.config.vlm_config import VLMConfig


def test_vlm_config_accepts_timeout():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=120.0)
    assert cfg.timeout == 120.0


def test_vlm_config_timeout_defaults_to_600():
    cfg = VLMConfig(model="gpt-4o-mini", api_key="sk-x")
    assert cfg.timeout == 600.0


def test_vlm_config_rejects_non_positive_timeout():
    with pytest.raises(ValidationError):
        VLMConfig(model="gpt-4o-mini", api_key="sk-x", timeout=0)


def test_build_openai_client_kwargs_default_timeout():
    kwargs = _build_openai_client_kwargs("openai", "sk-x", "https://example.invalid", None, None)
    assert kwargs["timeout"] == 600.0


def test_build_openai_client_kwargs_custom_timeout():
    kwargs = _build_openai_client_kwargs(
        "openai",
        "sk-x",
        "https://example.invalid",
        None,
        None,
        timeout=120.0,
    )
    assert kwargs["timeout"] == 120.0


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


def test_openai_vlm_defaults_to_600_timeout_when_config_omits_it():
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
        }
    )
    assert vlm.timeout == 600.0

    with mock.patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI") as fake:
        vlm.get_client()
    assert fake.call_args.kwargs.get("timeout") == 600.0
    assert "http_client" not in fake.call_args.kwargs


def test_litellm_build_kwargs_includes_timeout():
    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider

    vlm = LiteLLMVLMProvider(
        {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-x",
            "api_base": "https://example.invalid",
            "timeout": 90.0,
        }
    )
    kwargs = vlm._build_text_kwargs(prompt="hi")
    assert kwargs["timeout"] == 90.0


def test_vlm_config_propagates_timeout_to_codex_backend():
    cfg = VLMConfig(
        provider="openai-codex",
        model="gpt-5.3-codex",
        api_key="oauth-token",
        api_base="https://example.invalid/codex",
        timeout=45.0,
    )

    vlm = cfg.get_vlm_instance()

    assert vlm.timeout == 45.0


def test_codex_vlm_propagates_config_timeout():
    from openviking.models.vlm.backends.codex_vlm import CodexVLM
    from tests.unit.test_codex_vlm import _build_final_response, _MockResponsesStream

    vlm = CodexVLM(
        {
            "provider": "openai-codex",
            "model": "gpt-5.3-codex",
            "api_key": "oauth-token",
            "api_base": "https://example.invalid/codex",
            "timeout": 45.0,
        }
    )

    with mock.patch("openviking.models.vlm.backends.codex_vlm.openai.OpenAI") as fake:
        fake.return_value.responses.create.return_value = _MockResponsesStream(
            _build_final_response("timeout ok")
        )
        assert vlm.get_completion("hello") == "timeout ok"

    assert fake.call_args.kwargs.get("timeout") == 45.0
