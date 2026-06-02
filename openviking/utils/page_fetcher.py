# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Page fetcher abstraction with pluggable backends."""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import httpx

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FetchResult:
    html: str = ""
    status_code: int = 0
    final_url: str = ""
    content_type: Optional[str] = None
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 400 and self.error is None


class PageFetcher(ABC):
    @abstractmethod
    async def fetch(self, url: str, timeout: float = 10.0) -> FetchResult:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class SimpleFetcher(PageFetcher):
    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                headers={"User-Agent": "OpenViking/0.3"},
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def fetch(self, url: str, timeout: float = 10.0) -> FetchResult:
        try:
            client = await self._get_client()
            resp = await client.get(url, timeout=timeout)
            return FetchResult(
                html=resp.text,
                status_code=resp.status_code,
                final_url=str(resp.url),
                content_type=resp.headers.get("content-type", ""),
            )
        except Exception as e:
            logger.debug(f"[SimpleFetcher] Failed to fetch {url}: {e}")
            return FetchResult(html="", status_code=0, final_url=url, error=str(e))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class PlaywrightFetcher(PageFetcher):
    def __init__(self) -> None:
        self._playwright_manager = None
        self._browser = None
        self._browser_lock = asyncio.Lock()

    async def _get_browser(self):
        async with self._browser_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                if self._playwright_manager is None:
                    self._playwright_manager = await async_playwright().start()
                self._browser = await self._playwright_manager.chromium.launch(headless=True)
        return self._browser

    async def fetch(self, url: str, timeout: float = 30.0) -> FetchResult:
        page = None
        try:
            browser = await self._get_browser()
            page = await browser.new_page()
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout * 1000
            )
            # 等待一会儿，让 js 执行
            await page.wait_for_timeout(2000)
            html = await page.content()
            status = resp.status if resp else 200
            final_url = page.url
            return FetchResult(
                html=html,
                status_code=status,
                final_url=final_url,
            )
        except ImportError:
            return FetchResult(
                html="",
                status_code=0,
                final_url=url,
                error="playwright not installed, run: pip install playwright && playwright install chromium",
            )
        except Exception as e:
            logger.debug(f"[PlaywrightFetcher] Failed to fetch {url}: {e}")
            return FetchResult(html="", status_code=0, final_url=url, error=str(e))
        finally:
            if page:
                try:
                    await page.close()
                except Exception as e:
                    logger.debug(f"[PlaywrightFetcher] Failed to close page for {url}: {e}")

    async def close(self) -> None:
        async with self._browser_lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright_manager:
                await self._playwright_manager.stop()
                self._playwright_manager = None
