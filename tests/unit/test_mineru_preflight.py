# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for the MinerU startup preflight (wait_for_mineru_ready)."""

import httpx
import pytest

from openviking.service.mineru_preflight import wait_for_mineru_ready

ENDPOINT = "http://127.0.0.1:8000"
HEALTH_URL = "http://127.0.0.1:8000/health"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


def _make_fake_client(get_handler):
    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return get_handler(url)

    return _FakeClient


@pytest.fixture
def patch_httpx(monkeypatch):
    def _apply(get_handler):
        monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client(get_handler))

    return _apply


@pytest.mark.asyncio
async def test_ready_immediately(patch_httpx):
    patch_httpx(lambda url: _FakeResponse(payload={"protocol_version": 2, "status": "healthy"}))

    # Should return without raising.
    await wait_for_mineru_ready(ENDPOINT)


@pytest.mark.asyncio
async def test_ready_after_unhealthy(patch_httpx):
    calls = {"count": 0}

    def handler(url):
        calls["count"] += 1
        if calls["count"] == 1:
            return _FakeResponse(status_code=503, payload={"status": "starting"})
        return _FakeResponse(payload={"protocol_version": 2, "status": "healthy"})

    patch_httpx(handler)

    await wait_for_mineru_ready(ENDPOINT)
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_timeout_raises(patch_httpx):
    patch_httpx(
        lambda url: _FakeResponse(payload={"protocol_version": 2, "status": "unhealthy"})
    )

    with pytest.raises(RuntimeError) as exc_info:
        await wait_for_mineru_ready(ENDPOINT, timeout=0.05)

    message = str(exc_info.value)
    assert HEALTH_URL in message
    assert ENDPOINT in message
    assert "status='unhealthy'" in message


@pytest.mark.asyncio
async def test_network_error_raises(patch_httpx):
    def handler(url):
        raise httpx.ConnectError("connection refused")

    patch_httpx(handler)

    with pytest.raises(RuntimeError) as exc_info:
        await wait_for_mineru_ready(ENDPOINT, timeout=0.05)

    message = str(exc_info.value)
    assert HEALTH_URL in message
    assert "connection refused" in message


@pytest.mark.asyncio
async def test_empty_error_message_falls_back_to_class_name(patch_httpx):
    def handler(url):
        raise httpx.ConnectTimeout("")

    patch_httpx(handler)

    with pytest.raises(RuntimeError) as exc_info:
        await wait_for_mineru_ready(ENDPOINT, timeout=0.05)

    message = str(exc_info.value)
    assert HEALTH_URL in message
    assert "ConnectTimeout" in message
