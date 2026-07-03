# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Playwright renderer used only as low-content fallback."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from openviking.parse.accessors.web_crawler.render_heuristics import (
    CHALLENGE_MARKERS,
    SHELL_VISIBLE_TEXT_CHARS,
)


PLAYWRIGHT_PACKAGE_INSTALL_HINT = (
    "Playwright fallback was needed, but the Python package is not installed. "
    "Install it with `pip install playwright` and install Chromium with "
    "`python -m playwright install chromium`."
)
PLAYWRIGHT_CHROMIUM_INSTALL_HINT = (
    "Playwright fallback was needed, but Chromium is not installed or cannot be "
    "launched. Run `python -m playwright install chromium` and retry."
)

# Cap the networkidle wait: pages with continuous background activity (e.g. the
# GraphiQL playground, polling/websocket apps) never go idle and would otherwise
# block until the full render timeout. Content is usually ready right after
# domcontentloaded, and ``_wait_past_challenge`` handles late-arriving text.
_NETWORKIDLE_TIMEOUT_MS = 8000


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

            if self._request_validator:

                async def _validate_route(route):
                    await self._guard_route(route, self._request_validator)

                await page.route("**/*", _validate_route)

            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout * 1000,
            )
            try:
                await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
            except Exception:
                pass
            await self._wait_past_challenge(page, timeout * 1000)
            html = await self._read_content(page)
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
                try:
                    await page.close()
                except Exception:
                    pass

    @staticmethod
    async def _guard_route(route, request_validator) -> None:
        """SSRF guard for sub-resource requests.

        Block requests to disallowed hosts (e.g. private-network probe
        endpoints) by aborting them, but never let a blocked sub-resource
        fail the whole page render. Only the main document URL and the final
        URL (validated in ``render``) gate the overall result.
        """
        try:
            request_validator(route.request.url)
        except Exception:
            await route.abort()
            return
        await route.continue_()

    @staticmethod
    async def _wait_past_challenge(page, timeout_ms: float, poll_ms: int = 500) -> None:
        """Wait out JS anti-bot interstitials (e.g. "Please wait...").

        These challenge pages run a CPU-bound JS proof-of-work and then
        auto-redirect to the real content without further network activity,
        so ``networkidle`` returns while the interstitial is still showing.
        Poll the body text until real content appears or we run out of time.
        """
        import time

        deadline = time.monotonic() + max(timeout_ms, 0) / 1000
        while True:
            try:
                body = (await page.inner_text("body")).strip()
            except Exception:
                body = ""
            lowered = body.lower()
            looks_like_challenge = (
                not body
                or len(body) < SHELL_VISIBLE_TEXT_CHARS
                or any(marker in lowered for marker in CHALLENGE_MARKERS)
            )
            if not looks_like_challenge or time.monotonic() >= deadline:
                return
            await page.wait_for_timeout(poll_ms)

    @staticmethod
    async def _read_content(page) -> str:
        """Read page HTML, retrying through in-flight client-side navigation.

        Heavy SPAs (client-side redirects, late hydration) can still be
        navigating when we first ask for content, which raises "page is
        navigating and changing the content". Retry until the page settles.
        """
        last_exc = None
        for _ in range(6):
            try:
                return await page.content()
            except Exception as exc:
                if "navigating and changing the content" not in str(exc):
                    raise
                last_exc = exc
                try:
                    await page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
                await page.wait_for_timeout(400)
        if last_exc:
            raise last_exc
        return await page.content()

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
