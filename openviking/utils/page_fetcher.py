# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Page fetcher abstraction with pluggable backends."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import httpx
from openviking.utils.network_guard import build_httpx_request_validation_hooks

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

PLAYWRIGHT_PACKAGE_INSTALL_HINT = (
    "Playwright rendering was explicitly requested, but the Python package is "
    "not installed. Install it with `pip install playwright` or your project's "
    "package manager, then install Chromium with "
    "`python -m playwright install chromium`."
)
PLAYWRIGHT_CHROMIUM_INSTALL_HINT = (
    "Playwright rendering was explicitly requested, but Chromium is not "
    "installed or cannot be launched. Run "
    "`python -m playwright install chromium` "
    "(or `uv run python -m playwright install chromium` in a uv environment), "
    "then retry. You can also omit `use_playwright:true` to use the default "
    "static HTML fetcher."
)


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
    def __init__(self, request_validator: Optional[Callable[[str], None]] = None) -> None:
        self._client: Optional[httpx.AsyncClient] = None
        self._request_validator = request_validator

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            client_kwargs = {
                "follow_redirects": True,
                "headers": {"User-Agent": "OpenViking/0.3"},
                "timeout": httpx.Timeout(10.0),
            }
            event_hooks = build_httpx_request_validation_hooks(self._request_validator)
            if event_hooks:
                client_kwargs["event_hooks"] = event_hooks
                client_kwargs["trust_env"] = False
            self._client = httpx.AsyncClient(**client_kwargs)
        return self._client

    async def fetch(self, url: str, timeout: float = 10.0) -> FetchResult:
        try:
            if self._request_validator:
                self._request_validator(url)
            client = await self._get_client()
            resp = await client.get(url, timeout=timeout)
            final_url = str(resp.url)
            if self._request_validator:
                self._request_validator(final_url)
            return FetchResult(
                html=resp.text,
                status_code=resp.status_code,
                final_url=final_url,
                content_type=resp.headers.get("content-type", ""),
            )
        except Exception as e:
            logger.debug(f"[SimpleFetcher] Failed to fetch {url}: {e}")
            return FetchResult(html="", status_code=0, final_url=url, error=str(e))

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


class PlaywrightFetcher(PageFetcher):
    def __init__(self, request_validator: Optional[Callable[[str], None]] = None) -> None:
        self._playwright_manager = None
        self._browser = None
        self._browser_lock = asyncio.Lock()
        self._request_validator = request_validator

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
            if self._request_validator:
                self._request_validator(url)
            browser = await self._get_browser()
            page = await browser.new_page()
            validation_error = None
            if self._request_validator:
                # Browser pages may request subresources; validate those URLs too.
                async def _validate_route(route):
                    nonlocal validation_error
                    try:
                        self._request_validator(route.request.url)
                    except Exception as e:
                        validation_error = e
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", _validate_route)
            resp = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout * 1000
            )
            if validation_error:
                raise validation_error
            # Give client-side JavaScript a short chance to render content.
            await page.wait_for_timeout(2000)
            html = await page.content()
            status = resp.status if resp else 200
            final_url = page.url
            if self._request_validator:
                self._request_validator(final_url)
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
                error=PLAYWRIGHT_PACKAGE_INSTALL_HINT,
            )
        except Exception as e:
            logger.debug(f"[PlaywrightFetcher] Failed to fetch {url}: {e}")
            error = str(e)
            if (
                "Executable doesn't exist" in error
                or "playwright install" in error
                or "BrowserType.launch" in error
            ):
                error = PLAYWRIGHT_CHROMIUM_INSTALL_HINT
            return FetchResult(html="", status_code=0, final_url=url, error=error)
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
