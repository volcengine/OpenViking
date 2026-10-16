"""Regression tests for web_fetch network target validation."""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest
from vikingbot.agent.tools import web


@pytest.fixture(autouse=True)
def stub_readability(monkeypatch: pytest.MonkeyPatch) -> None:
    """web_fetch imports readability lazily; tests do not need the real package."""

    module = types.ModuleType("readability")

    class Document:  # pragma: no cover - only needed if an HTML branch is exercised
        def __init__(self, text: str) -> None:
            self.text = text

        def summary(self) -> str:
            return self.text

        def title(self) -> str:
            return ""

    module.Document = Document  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "readability", module)


@pytest.mark.asyncio
async def test_web_fetch_rejects_loopback_before_http_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loopback URLs must be rejected before any server-side request is made."""

    class FailingClient:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("web_fetch should not create an HTTP client for blocked targets")

    monkeypatch.setattr(web.httpx, "AsyncClient", FailingClient)

    result = json.loads(await web.WebFetchTool().execute(None, url="http://127.0.0.1:8080/secret"))

    assert "error" in result
    assert "URL validation failed" in result["error"]
    assert "non-public" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_validates_redirect_requests_with_httpx_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The httpx request hook must validate the initial request and every redirect hop."""

    validated_urls: list[str] = []

    def tracking_validator(url: str) -> None:
        validated_urls.append(url)

    monkeypatch.setattr(web, "ensure_public_remote_target", tracking_validator)

    class DummyResponse:
        headers = {"content-type": "text/plain"}
        text = "public body"
        status_code = 200
        url = "https://example.com/final"

        def raise_for_status(self) -> None:
            return None

    class DummyRequest:
        url = "https://example.com/redirect-hop"

    class RecordingClient:
        def __init__(self, *_args: Any, **kwargs: Any) -> None:
            self.event_hooks = kwargs["event_hooks"]
            assert self.event_hooks is not None
            assert "request" in self.event_hooks

        async def __aenter__(self) -> "RecordingClient":
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> DummyResponse:
            await self.event_hooks["request"][0](DummyRequest())
            return DummyResponse()

    monkeypatch.setattr(web.httpx, "AsyncClient", RecordingClient)

    result = json.loads(await web.WebFetchTool().execute(None, url="https://example.com/start"))

    assert result["text"] == "public body"
    assert validated_urls == ["https://example.com/start", "https://example.com/redirect-hop"]
