# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for page fetcher error handling."""

import pytest

from openviking.utils.page_fetcher import (
    PLAYWRIGHT_CHROMIUM_INSTALL_HINT,
    PLAYWRIGHT_PACKAGE_INSTALL_HINT,
    PlaywrightFetcher,
)


class FakePlaywrightResponse:
    status = 200
    headers = {"content-type": "text/html; charset=utf-8"}


class FakePlaywrightPage:
    url = "https://example.com/final"

    async def goto(self, url, wait_until, timeout):
        return FakePlaywrightResponse()

    async def wait_for_timeout(self, timeout):
        return None

    async def content(self):
        return "<html><body>ok</body></html>"

    async def close(self):
        return None


class FakePlaywrightBrowser:
    async def new_page(self):
        return FakePlaywrightPage()


@pytest.mark.asyncio
async def test_playwright_fetcher_reports_missing_package(monkeypatch):
    fetcher = PlaywrightFetcher()

    async def raise_import_error():
        raise ImportError("No module named playwright")

    monkeypatch.setattr(fetcher, "_get_browser", raise_import_error)

    result = await fetcher.fetch("https://example.com")

    assert result.status_code == 0
    assert result.final_url == "https://example.com"
    assert result.error == PLAYWRIGHT_PACKAGE_INSTALL_HINT


@pytest.mark.asyncio
async def test_playwright_fetcher_reports_missing_chromium(monkeypatch):
    fetcher = PlaywrightFetcher()

    async def raise_chromium_error():
        raise RuntimeError("Executable doesn't exist at /tmp/chromium")

    monkeypatch.setattr(fetcher, "_get_browser", raise_chromium_error)

    result = await fetcher.fetch("https://example.com")

    assert result.status_code == 0
    assert result.final_url == "https://example.com"
    assert result.error == PLAYWRIGHT_CHROMIUM_INSTALL_HINT


@pytest.mark.asyncio
async def test_playwright_fetcher_preserves_content_type(monkeypatch):
    fetcher = PlaywrightFetcher()

    async def fake_get_browser():
        return FakePlaywrightBrowser()

    monkeypatch.setattr(fetcher, "_get_browser", fake_get_browser)

    result = await fetcher.fetch("https://example.com")

    assert result.status_code == 200
    assert result.final_url == "https://example.com/final"
    assert result.content_type == "text/html; charset=utf-8"
