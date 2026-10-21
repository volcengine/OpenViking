# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the VikingBot web_fetch tool."""

import json

import pytest
from vikingbot.agent.loop import _is_tool_result_success
from vikingbot.agent.tools import web
from vikingbot.agent.tools.base import ToolContext


def _error_payload(result: str) -> dict:
    assert result.startswith("Error: ")
    assert _is_tool_result_success(result) is False
    return json.loads(result.removeprefix("Error: "))


@pytest.mark.asyncio
async def test_web_fetch_invalid_url_returns_failed_tool_result():
    result = await web.WebFetchTool().execute(ToolContext(), "ftp://example.com/private")

    assert _error_payload(result) == {
        "error": "URL validation failed: Only http/https allowed, got 'ftp'",
        "url": "ftp://example.com/private",
    }


@pytest.mark.asyncio
async def test_web_fetch_request_exception_returns_failed_tool_result(monkeypatch):
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, *_args, **_kwargs):
            raise RuntimeError("fetch failed")

    monkeypatch.setattr(web.httpx, "AsyncClient", lambda **_kwargs: FailingClient())

    result = await web.WebFetchTool().execute(ToolContext(), "https://example.com/")

    assert _error_payload(result) == {
        "error": "fetch failed",
        "url": "https://example.com/",
    }
