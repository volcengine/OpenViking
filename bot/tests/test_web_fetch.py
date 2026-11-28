# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the VikingBot web_fetch tool."""

import json

import httpx
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


_ESCAPED_MARKUP_PAGE = """<!doctype html><html><head><title>Escaping</title></head><body><article>
<h2>The &lt;div&gt; element</h2>
<p>To print a literal tag, write &amp;lt;br&amp;gt; in your source.</p>
<ul><li>Use List&lt;String&gt; for names</li></ul>
<p>See <a href="/d">the &lt;div&gt; docs</a>. Tom &amp; Jerry &copy; 2026, a &lt; b.</p>
</article></body></html>"""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extract_mode", "expected_lines"),
    [
        (
            "markdown",
            [
                "## The <div> element",
                "To print a literal tag, write &lt;br&gt; in your source.",
                "- Use List<String> for names",
                "See [the <div> docs](/d). Tom & Jerry © 2026, a < b.",
            ],
        ),
        (
            "text",
            [
                "The <div> element",
                "To print a literal tag, write &lt;br&gt; in your source.",
                "Use List<String> for names",
                "See the <div> docs. Tom & Jerry © 2026, a < b.",
            ],
        ),
    ],
)
async def test_web_fetch_decodes_page_text_once(monkeypatch, extract_mode, expected_lines):
    real_client = web.httpx.AsyncClient

    def serve_page(_request):
        return httpx.Response(200, headers={"content-type": "text/html"}, text=_ESCAPED_MARKUP_PAGE)

    monkeypatch.setattr(
        web.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(serve_page), **kwargs),
    )

    result = await web.WebFetchTool().execute(
        ToolContext(), "https://example.com/", extractMode=extract_mode
    )

    lines = [line for line in json.loads(result)["text"].splitlines() if line.strip()]
    assert lines == ["# Escaping", *expected_lines]
