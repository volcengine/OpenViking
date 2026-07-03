# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scrapy spider for recursive web resource import."""

import asyncio
import os
from collections.abc import Iterator
from urllib.parse import urljoin, urlparse

import scrapy
from parsel import Selector
from scrapy import signals
from scrapy.exceptions import CloseSpider

from openviking.parse.accessors.http_accessor import URLType, URLTypeDetector
from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.models import CrawledDownload, CrawledPage
from openviking.parse.accessors.web_crawler.playwright_renderer import PlaywrightRenderer
from openviking.parse.accessors.web_crawler.render_heuristics import (
    looks_like_unrendered_page,
    should_render_with_playwright,
)


_DOWNLOAD_URL_TYPES = frozenset(
    {
        URLType.DOWNLOAD_PDF,
        URLType.DOWNLOAD_MD,
        URLType.DOWNLOAD_TXT,
        URLType.DOWNLOAD_DOCUMENT,
    }
)
_DOWNLOAD_EXTENSIONS = frozenset(
    ext
    for ext, url_type in URLTypeDetector.EXTENSION_MAP.items()
    if url_type in _DOWNLOAD_URL_TYPES
)


class OpenVikingWebSpider(scrapy.Spider):
    name = "openviking_web"

    ASSET_EXTENSIONS = frozenset(
        {
            ".css",
            ".js",
            ".map",
            ".mjs",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".ico",
            ".webp",
            ".avif",
            ".bmp",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".mp3",
            ".mp4",
        }
    )
    DOWNLOAD_EXTENSIONS = _DOWNLOAD_EXTENSIONS

    def __init__(
        self,
        root_url: str,
        config: CrawlConfig,
        collector: list[CrawledPage],
        download_collector: list[CrawledDownload],
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.root_url = root_url
        self.config = config
        self.collector = collector
        self.download_collector = download_collector
        self.renderer = PlaywrightRenderer(config.request_validator)
        self.root_host = urlparse(root_url).netloc.lower()
        self._success_count = 0
        self._seen_download_urls: set[str] = set()
        self._render_semaphore: asyncio.Semaphore | None = None

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider._on_spider_closed, signal=signals.spider_closed)
        return spider

    async def _on_spider_closed(self, spider, reason):
        try:
            await asyncio.wait_for(self.renderer.close(), timeout=5.0)
        except Exception:
            pass

    def _success_at_limit(self) -> bool:
        return 0 < self.config.max_pages <= self._success_count

    def _stop_if_success_at_limit(self) -> bool:
        if not self._success_at_limit():
            return False
        if getattr(self, "crawler", None) and getattr(self.crawler, "engine", None):
            raise CloseSpider("max_pages_reached")
        return True

    async def start(self):
        yield scrapy.Request(self.root_url, callback=self.parse, errback=self.errback)

    async def parse(self, response):
        if self._stop_if_success_at_limit():
            return
        final_url = response.url
        depth = int(response.meta.get("depth", 0) or 0)
        try:
            self._validate_url(final_url)
        except Exception as exc:
            self._add_failed(response.url, final_url, depth, str(exc))
            return

        if not self._is_html_response(response):
            self._add_skipped(final_url, depth, "non-html response")
            return
        if len(response.body or b"") > self.config.max_html_bytes:
            self._add_skipped(final_url, depth, "html too large")
            return

        page_html = response.text
        page_source = "scrapy_static"
        needs_render = self.config.fallback_playwright and should_render_with_playwright(
            response.text
        )
        try:
            if needs_render:
                if self._render_semaphore is None:
                    self._render_semaphore = asyncio.Semaphore(self.config.concurrency)
                async with self._render_semaphore:
                    if self._success_at_limit():
                        return
                    rendered = await self.renderer.render(
                        final_url, self.config.playwright_timeout
                    )
                if rendered.is_success and rendered.html:
                    final_url = rendered.final_url or final_url
                    page_html = rendered.html
                    page_source = "playwright"
                elif rendered.error:
                    self._add_failed(response.url, final_url, depth, rendered.error)
                    return
        except Exception as exc:
            self._add_failed(response.url, final_url, depth, str(exc))
            return

        if needs_render and looks_like_unrendered_page(page_html):
            self._add_failed(
                response.url,
                final_url,
                depth,
                "page did not render real content (empty shell or anti-bot challenge page)",
            )
            return

        if self._stop_if_success_at_limit():
            return
        self.collector.append(
            CrawledPage(
                url=response.url,
                final_url=final_url,
                depth=depth,
                html=page_html,
                source=page_source,
            )
        )
        self._success_count += 1

        if self._stop_if_success_at_limit():
            return
        if self.config.depth >= 0 and depth >= self.config.depth:
            return
        for url in self._extract_download_urls(page_html, final_url, depth + 1):
            self.download_collector.append(CrawledDownload(url=url, depth=depth + 1))
            self._success_count += 1
            if self._stop_if_success_at_limit():
                return
        for url in self._extract_child_urls(page_html, final_url):
            yield response.follow(
                url,
                callback=self.parse,
                errback=self.errback,
            )

    def errback(self, failure):
        request = failure.request
        depth = int(request.meta.get("depth", 0) or 0)
        self._add_failed(request.url, request.url, depth, failure.getErrorMessage())

    def _iter_unique_links(self, html: str, base_url: str) -> Iterator[str]:
        try:
            hrefs = Selector(text=html or "").css("a::attr(href)").getall()
        except Exception:
            hrefs = []
        seen: set[str] = set()
        for href in hrefs:
            href = (href or "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            url = urljoin(base_url, href).split("#", 1)[0]
            if url in seen:
                continue
            seen.add(url)
            yield url

    def _extract_child_urls(self, html: str, base_url: str) -> list[str]:
        accepted: list[str] = []
        for url in self._iter_unique_links(html, base_url):
            if self._accept_child_url(url):
                accepted.append(url)
                if len(accepted) >= self.config.max_links_per_page:
                    break
        return accepted

    def _extract_download_urls(self, html: str, base_url: str, depth: int) -> list[str]:
        if self.config.skip_download_links:
            return []
        accepted: list[str] = []
        for url in self._iter_unique_links(html, base_url):
            if self._success_at_limit():
                break
            if url in self._seen_download_urls or not self._accept_download_url(url):
                continue
            self._seen_download_urls.add(url)
            accepted.append(url)
            if len(accepted) >= self.config.max_links_per_page:
                break
        return accepted

    def _accept_child_url(self, url: str) -> bool:
        parsed = urlparse(url)
        ext = os.path.splitext(parsed.path.lower())[1]
        if ext in self.ASSET_EXTENSIONS or ext in self.DOWNLOAD_EXTENSIONS:
            return False
        return self._passes_url_filters(url, parsed)

    def _accept_download_url(self, url: str) -> bool:
        parsed = urlparse(url)
        ext = os.path.splitext(parsed.path.lower())[1]
        if ext not in self.DOWNLOAD_EXTENSIONS:
            return False
        return self._passes_url_filters(url, parsed)

    def _passes_url_filters(self, url: str, parsed) -> bool:
        if parsed.scheme not in ("http", "https"):
            return False
        if not self.config.allow_external_links and parsed.netloc.lower() != self.root_host:
            return False
        if self.config.include_paths and not self._matches_any(
            parsed.path, self.config.include_paths
        ):
            return False
        if self.config.exclude_paths and self._matches_any(parsed.path, self.config.exclude_paths):
            return False
        try:
            self._validate_url(url)
        except Exception:
            return False
        return True

    def _validate_url(self, url: str) -> None:
        if self.config.request_validator:
            self.config.request_validator(url)

    @staticmethod
    def _matches_any(path: str, patterns: list[str]) -> bool:
        return any(pattern and path.startswith(pattern) for pattern in patterns)

    @staticmethod
    def _is_html_response(response) -> bool:
        content_type = response.headers.get("content-type", b"").decode("latin1").lower()
        return not content_type or "html" in content_type

    def _add_skipped(self, url: str, depth: int, reason: str) -> None:
        self.collector.append(
            CrawledPage(url=url, final_url=url, depth=depth, status="skipped", error=reason)
        )

    def _add_failed(self, url: str, final_url: str, depth: int, error: str) -> None:
        self.collector.append(
            CrawledPage(
                url=url,
                final_url=final_url,
                depth=depth,
                status="failed",
                error=error,
            )
        )
