# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Web search backend selection and the keyless Keenable backend."""

import httpx
import pytest
from vikingbot.agent.tools.websearch import registry
from vikingbot.agent.tools.websearch.keenable import KeenableBackend

KEY_ENV_VARS = ("BRAVE_API_KEY", "EXA_API_KEY", "TAVILY_API_KEY", "KEENABLE_API_KEY")


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch):
    for var in KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_auto_prefers_keenable_over_ddgs_when_no_key_is_configured():
    assert registry.select_auto().name == "keenable"


def test_auto_prefers_a_configured_key_over_keenable():
    assert registry.select_auto(brave_api_key="brave-key").name == "brave"


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}
        self.request = None

    async def handle_async_request(self, request):
        self.request = request
        return httpx.Response(self.status_code, json=self.payload)


@pytest.fixture
def transport(monkeypatch):
    holder = {}
    real_client = httpx.AsyncClient

    def fake_client(**kwargs):
        return real_client(transport=holder["transport"], **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fake_client)

    def install(**kwargs):
        holder["transport"] = _Transport(**kwargs)
        return holder["transport"]

    return install


async def test_keyless_search_uses_public_endpoint_and_reads_snippet(transport):
    t = transport(
        payload={
            "results": [
                {
                    "title": "OpenViking",
                    "url": "https://example.com/ov",
                    "description": "",
                    "snippet": "context\ndatabase  for agents",
                }
            ]
        }
    )

    result = await KeenableBackend().search("openviking", count=3)

    assert t.request.url == "https://api.keenable.ai/v1/search/public"
    assert t.request.headers["X-Keenable-Title"] == "openviking"
    assert "X-API-Key" not in t.request.headers
    assert result == (
        "Results for: openviking\n\n"
        "1. OpenViking\n   https://example.com/ov\n   context database for agents"
    )


async def test_keyed_search_uses_keyed_endpoint(transport):
    t = transport(payload={"results": []})

    result = await KeenableBackend(api_key="secret").search("openviking", count=3)

    assert t.request.url == "https://api.keenable.ai/v1/search"
    assert t.request.headers["X-API-Key"] == "secret"
    assert result == "No results for: openviking"


async def test_rate_limit_is_reported_as_a_backend_error(transport):
    transport(status_code=429, payload={"error": "Rate limit exceeded"})

    result = await KeenableBackend().search("openviking", count=3)

    assert result.startswith("Error: ")
    assert "429" in result
