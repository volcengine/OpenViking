# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Playwright renderer used only as low-content fallback."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional


PLAYWRIGHT_PACKAGE_INSTALL_HINT = (
    "Playwright fallback was needed, but the Python package is not installed. "
    "Install it with `pip install playwright` and install Chromium with "
    "`python -m playwright install chromium`."
)
PLAYWRIGHT_CHROMIUM_INSTALL_HINT = (
    "Playwright fallback was needed, but Chromium is not installed or cannot be "
    "launched. Run `python -m playwright install chromium` and retry."
)


@dataclass
class RenderResult:
    html: str = ""
    status_code: int = 0
    final_url: str = ""
    content_type: str = ""
    error: Optional[str] = None

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 400 and self.error is None


class PlaywrightRenderer:
    def __init__(self, request_validator: Optional[Callable[[str], None]] = None) -> None:
        self._request_validator = request_validator
        self._playwright_manager = None
        self._browser = None
        self._browser_lock = asyncio.Lock()

    async def render(self, url: str, timeout: float) -> RenderResult:
        page = None
        try:
            if self._request_validator:
                self._request_validator(url)
            browser = await self._get_browser()
            page = await browser.new_page(accept_downloads=False)
            validation_error = None

            if self._request_validator:

                async def _validate_route(route):
                    nonlocal validation_error
                    try:
                        self._request_validator(route.request.url)
                    except Exception as exc:
                        validation_error = exc
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", _validate_route)

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )
            await page.wait_for_timeout(1500)
            if validation_error:
                raise validation_error
            html = await page.content()
            final_url = page.url
            if self._request_validator:
                self._request_validator(final_url)
            return RenderResult(
                html=html,
                status_code=response.status if response else 200,
                final_url=final_url,
                content_type=response.headers.get("content-type", "") if response else "",
            )
        except ImportError:
            return RenderResult(final_url=url, error=PLAYWRIGHT_PACKAGE_INSTALL_HINT)
        except Exception as exc:
            error = str(exc)
            if (
                "Executable doesn't exist" in error
                or "playwright install" in error
                or "BrowserType.launch" in error
            ):
                error = PLAYWRIGHT_CHROMIUM_INSTALL_HINT
            return RenderResult(final_url=url, error=error)
        finally:
            if page:
                await page.close()

    async def close(self) -> None:
        async with self._browser_lock:
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._playwright_manager:
                await self._playwright_manager.stop()
                self._playwright_manager = None

    async def _get_browser(self):
        async with self._browser_lock:
            if self._browser is None or not self._browser.is_connected():
                from playwright.async_api import async_playwright

                if self._playwright_manager is None:
                    self._playwright_manager = await async_playwright().start()
                self._browser = await self._playwright_manager.chromium.launch(headless=True)
        return self._browser
