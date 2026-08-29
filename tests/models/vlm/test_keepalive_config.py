# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for OpenAI-compatible VLM keepalive configuration."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterator
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from pydantic import ValidationError

from openviking.models.vlm.backends.openai_vlm import OpenAIVLM
from openviking_cli.utils.config.vlm_config import VLMConfig


class _StaleConnectionServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _StaleConnectionHandler)
        self.connection_count = 0
        self.connection_numbers = {}

    def get_request(self):
        request, address = super().get_request()
        self.connection_count += 1
        self.connection_numbers[id(request)] = self.connection_count
        return request, address


class _StaleConnectionHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self) -> None:
        super().setup()
        self.connection_number = self.server.connection_numbers.pop(id(self.request))
        self.request_count = 0

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(content_length)
        self.request_count += 1

        if self.connection_number == 1 and self.request_count > 1:
            time.sleep(0.5)
            self.close_connection = True
            return

        payload = json.dumps(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-vlm",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _stale_connection_server() -> Iterator[_StaleConnectionServer]:
    server = _StaleConnectionServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    "config_kwargs",
    [
        {
            "provider": "openai",
            "api_key": "test-key",
            "keepalive_expiry": 0,
        },
        {
            "providers": {
                "openai": {
                    "api_key": "test-key",
                    "keepalive_expiry": 0,
                }
            },
        },
        {
            "credentials": [
                {
                    "provider": "openai",
                    "api_key": "test-key",
                    "keepalive_expiry": 0,
                }
            ],
        },
    ],
)
def test_vlm_config_propagates_keepalive_expiry(config_kwargs) -> None:
    config = VLMConfig(model="test-vlm", **config_kwargs)

    assert config.get_vlm_instance().keepalive_expiry == 0


def test_vlm_config_rejects_negative_keepalive_expiry() -> None:
    with pytest.raises(ValidationError):
        VLMConfig(
            model="test-vlm",
            provider="openai",
            api_key="test-key",
            keepalive_expiry=-0.1,
        )


@patch("openviking.models.vlm.backends.openai_vlm.openai.OpenAI")
def test_openai_vlm_uses_sdk_transport_when_keepalive_is_unset(mock_openai) -> None:
    mock_openai.return_value = MagicMock()
    vlm = OpenAIVLM(
        {
            "provider": "openai",
            "model": "test-vlm",
            "api_key": "test-key",
        }
    )

    vlm.get_client()

    assert vlm.keepalive_expiry is None
    assert "http_client" not in mock_openai.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "azure"])
async def test_openai_vlm_applies_keepalive_to_owned_clients(provider: str) -> None:
    provider_kwargs = {}
    if provider == "azure":
        provider_kwargs = {
            "api_base": "https://test-resource.openai.azure.com",
            "api_version": "2025-01-01-preview",
        }

    vlm = OpenAIVLM(
        {
            "provider": provider,
            "model": "test-vlm",
            "api_key": "test-key",
            "keepalive_expiry": 0,
            "timeout": 12.0,
            **provider_kwargs,
        }
    )
    sync_client = vlm.get_client()
    async_client = vlm.get_async_client()

    sync_pool = sync_client._client._transport._pool
    async_pool = async_client._client._transport._pool
    assert sync_pool._max_connections == openai.DEFAULT_CONNECTION_LIMITS.max_connections
    assert (
        sync_pool._max_keepalive_connections
        == openai.DEFAULT_CONNECTION_LIMITS.max_keepalive_connections
    )
    assert sync_pool._keepalive_expiry == 0
    assert sync_client._client.timeout == httpx.Timeout(12.0)
    assert async_pool._max_connections == openai.DEFAULT_CONNECTION_LIMITS.max_connections
    assert (
        async_pool._max_keepalive_connections
        == openai.DEFAULT_CONNECTION_LIMITS.max_keepalive_connections
    )
    assert async_pool._keepalive_expiry == 0
    assert async_client._client.timeout == httpx.Timeout(12.0)

    vlm.close()
    await asyncio.sleep(0)

    assert sync_client.is_closed()
    assert async_client.is_closed()


@pytest.mark.asyncio
async def test_zero_keepalive_avoids_reusing_stale_local_connection() -> None:
    with _stale_connection_server() as server:
        vlm = OpenAIVLM(
            {
                "provider": "openai",
                "model": "test-vlm",
                "api_key": "test-key",
                "api_base": f"http://127.0.0.1:{server.server_port}/v1",
                "timeout": 0.1,
                "max_retries": 0,
                "keepalive_expiry": 0,
            }
        )
        async_client = vlm.get_async_client()

        try:
            assert await vlm.get_completion_async("first") == "ok"
            assert await vlm.get_completion_async("second") == "ok"
            assert server.connection_count == 2
        finally:
            if not async_client.is_closed():
                await async_client.close()
