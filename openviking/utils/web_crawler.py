# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Web crawler orchestrator with BFS traversal and SSR-first strategy."""

import asyncio
from dataclasses import dataclass, field
from typing import Any, List, Optional, Set

from openviking.utils.crawl_filter import CrawlConfig, CrawlFilter
from openviking.utils.link_extractor import LinkExtractor
from openviking.utils.page_fetcher import FetchResult, PageFetcher, SimpleFetcher, PlaywrightFetcher
from openviking.utils.ssr_extractor import SSRDataExtractor

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class CrawledPage:
    url: str = ""
    depth: int = 0
    title: Optional[str] = None
    status: str = "success"
    content: Optional[str] = None
    content_type: Optional[str] = None
    source: str = "html"


@dataclass
class CrawlResult:
    pages: List[CrawledPage] = field(default_factory=list)
    total_discovered: int = 0
    total_crawled: int = 0
    total_skipped: int = 0
    total_failed: int = 0


class WebCrawler:
    SPA_EMPTY_PATTERNS = (
        "You need to enable JavaScript to run this app.",
        "This app works best with JavaScript enabled.",
        "Please enable JavaScript to continue.",
        "JavaScript is required to use this application.",
        "Enable JavaScript to view this page.",
    )

    def __init__(
        self,
        config: CrawlConfig,
        fetcher: Optional[PageFetcher] = None,
    ) -> None:
        self.config = config
        
        if fetcher:
            self._fetcher = fetcher
        elif getattr(self.config, 'use_playwright', False):
            self._fetcher = PlaywrightFetcher(self.config.request_validator)
            logger.info("WebCrawler using PlaywrightFetcher")
        else:
            self._fetcher = SimpleFetcher(self.config.request_validator)
            logger.info("WebCrawler using SimpleFetcher")

        self._filter = CrawlFilter(config)
        self._link_extractor = LinkExtractor()
        self._ssr_extractor = SSRDataExtractor()
        self._pages_crawled: int = 0
        self._pages_scheduled: int = 0

    def _is_empty_spa_page(self, html: str) -> bool:
        html_lower = html.lower().strip()
        
        has_js_required_pattern = any(
            pattern.lower() in html_lower for pattern in self.SPA_EMPTY_PATTERNS
        )
        
        if not has_js_required_pattern:
            return False
        
        # 页面包含 JS 提示，但我们还需要确认它是否真的没有实际内容
        # 如果 HTML 很小（比如 < 1000 字符），那肯定是空壳
        if len(html_lower) < 1000:
            return True
            
        from bs4 import BeautifulSoup
        try:
            soup = BeautifulSoup(html, "html.parser")
            body = soup.body
            if not body:
                return True
            
            # 移除 script 和 noscript 后的纯文本
            for el in body(["script", "noscript", "style"]):
                el.decompose()
                
            text_content = body.get_text(strip=True)
            if len(text_content) < 200:
                return True
                
        except Exception:
            pass
        
        return False

    async def crawl(
        self,
        root_url: str,
        seed_html: Optional[str] = None,
    ) -> CrawlResult:
        result = CrawlResult()
        queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()

        if seed_html:
            self._filter.add_visited(root_url)
            child_urls = self._extract_urls_from_html(seed_html, root_url)
            for url in child_urls:
                await queue.put((url, 1))
            self._pages_crawled = 1
            self._pages_scheduled = 1
            result.total_discovered = len(child_urls) + 1
        else:
            await queue.put((root_url, 0))

        semaphore = asyncio.Semaphore(self.config.concurrency)
        active_tasks: Set[asyncio.Task[Any]] = set()

        while not queue.empty() or active_tasks:
            while not queue.empty() and len(active_tasks) < self.config.concurrency:
                if self._is_schedule_limit_reached():
                    break
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                url, depth = item
                if depth > self.config.depth:
                    result.total_skipped += 1
                    continue
                self._pages_scheduled += 1
                task = asyncio.create_task(self._crawl_one(url, depth, semaphore))
                active_tasks.add(task)

            if not active_tasks:
                break

            done, active_tasks = await asyncio.wait(
                active_tasks, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                page, child_urls = task.result()
                result.pages.append(page)
                if page.status == "success":
                    result.total_crawled += 1
                    self._pages_crawled += 1
                    for child_url in child_urls:
                        await queue.put((child_url, page.depth + 1))
                elif page.status == "skipped":
                    result.total_skipped += 1
                else:
                    result.total_failed += 1

        result.total_discovered = (
            result.total_crawled + result.total_skipped + result.total_failed
        )
        logger.info(
            f"[Crawl] Finished: crawled={result.total_crawled} "
            f"skipped={result.total_skipped} failed={result.total_failed}"
        )
        return result

    def _is_schedule_limit_reached(self) -> bool:
        return self.config.max_pages > 0 and self._pages_scheduled >= self.config.max_pages

    async def _crawl_one(
        self,
        url: str,
        depth: int,
        semaphore: asyncio.Semaphore,
    ) -> tuple[CrawledPage, List[str]]:
        async with semaphore:
            try:
                fetch_result: FetchResult = await self._fetcher.fetch(url)
                if not fetch_result.is_success:
                    return (
                        CrawledPage(
                            url=url, depth=depth, status="failed", content=fetch_result.error
                        ),
                        [],
                    )

                canonical_url = fetch_result.final_url or url
                if (
                    self._filter.normalize_url(canonical_url)
                    != self._filter.normalize_url(url)
                ):
                    if self._filter.is_visited(canonical_url):
                        logger.info(
                            f"[Crawl][RedirectDedup] skipped duplicate final URL: "
                            f"{url} -> {canonical_url}"
                        )
                        return (
                            CrawledPage(
                                url=canonical_url,
                                depth=depth,
                                status="skipped",
                                content="Duplicate final URL after redirect",
                            ),
                            [],
                        )
                    self._filter.add_visited(canonical_url)
                else:
                    self._filter.add_visited(canonical_url)

                if not self._filter.filter_by_content_type(canonical_url, fetch_result.content_type):
                    logger.info(
                        f"[Crawl][ContentTypeFilter] skipped non-HTML: {canonical_url} "
                        f"(content-type: {fetch_result.content_type})"
                    )
                    return (
                        CrawledPage(
                            url=canonical_url,
                            depth=depth,
                            status="skipped",
                            content_type=fetch_result.content_type,
                        ),
                        [],
                    )

                ssr_result = self._ssr_extractor.extract(fetch_result.html, canonical_url)
                if ssr_result:
                    markdown_content = None
                    if ssr_result.docs and ssr_result.docs[0].content:
                        markdown_content = ssr_result.docs[0].content
                    
                    page = CrawledPage(
                        url=canonical_url,
                        depth=depth,
                        status="success",
                        title=ssr_result.docs[0].title if ssr_result.docs else None,
                        content=markdown_content or fetch_result.html,
                        content_type="text/markdown" if markdown_content else "text/html",
                        source="ssr" if markdown_content else "html"
                    )
                    child_urls = ssr_result.child_urls
                    logger.info(
                        f"[Crawl][SSR] depth={depth} url={canonical_url} "
                        f"children={len(child_urls)} docs={len(ssr_result.docs)}"
                    )
                    return (page, child_urls)

                if self._is_empty_spa_page(fetch_result.html):
                    logger.info(
                        f"[Crawl][EmptySPA] skipped empty SPA page: {canonical_url}"
                    )
                    return (
                        CrawledPage(
                            url=canonical_url,
                            depth=depth,
                            status="skipped",
                            content="Empty SPA page - JavaScript required",
                        ),
                        [],
                    )

                child_urls = self._extract_urls_from_html(fetch_result.html, canonical_url)
                page = CrawledPage(
                    url=canonical_url,
                    depth=depth,
                    status="success",
                    content=fetch_result.html,
                    content_type="text/html",
                    source="html"
                )
                logger.info(
                    f"[Crawl][HTML] depth={depth} url={canonical_url} children={len(child_urls)}"
                )
                return (page, child_urls)

            except Exception as e:
                logger.warning(f"[Crawl] Failed: {url} - {e}")
                return (
                    CrawledPage(url=url, depth=depth, status="failed", content=str(e)),
                    [],
                )

    def _extract_urls_from_html(self, html: str, base_url: str) -> List[str]:
        extract_result = self._link_extractor.extract(html, base_url)
        filter_result = self._filter.filter(extract_result.urls, base_url)
        stats = filter_result.stats
        logger.debug(
            f"[Filter] total={extract_result.total_links_found} "
            f"accepted={len(filter_result.accepted)} "
            f"scheme={stats.rejected_by_scheme} static={stats.rejected_by_static} "
            f"domain={stats.rejected_by_domain} path={stats.rejected_by_path} "
            f"dedup={stats.rejected_by_dedup} limit={stats.rejected_by_limit}"
        )
        return filter_result.accepted

    async def close(self) -> None:
        """关闭爬虫资源"""
        if hasattr(self._fetcher, 'close'):
            await self._fetcher.close()
