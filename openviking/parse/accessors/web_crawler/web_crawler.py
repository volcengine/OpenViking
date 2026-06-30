# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Scrapy-backed recursive web crawler."""

import asyncio
import multiprocessing
import queue

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.models import (
    CrawledDownload,
    CrawledPage,
    CrawlResult,
)


def _run_crawl_worker(root_url: str, config: CrawlConfig, result_queue) -> None:
    import asyncio as _asyncio

    from scrapy.crawler import CrawlerProcess
    from scrapy.settings import Settings

    from openviking.parse.accessors.web_crawler.scrapy_spider import OpenVikingWebSpider

    pages: list[CrawledPage] = []
    downloads: list[CrawledDownload] = []
    process = CrawlerProcess(_build_settings(config))
    crawler = process.create_crawler(OpenVikingWebSpider)
    process.crawl(
        crawler,
        root_url=root_url,
        config=config,
        collector=pages,
        download_collector=downloads,
    )
    try:
        process.start()
    except Exception as exc:
        result_queue.put((pages, downloads, str(exc)))
        return
    if crawler.spider is not None:
        try:
            _asyncio.run(crawler.spider.renderer.close())
        except Exception:
            pass
    result_queue.put((pages, downloads, None))


def _build_settings(config: CrawlConfig) -> Settings:
    from scrapy.settings import Settings

    depth_limit = 0 if config.depth < 0 else config.depth
    close_pagecount = 0 if config.max_pages < 0 else config.max_pages
    return Settings(
        {
            "DEPTH_LIMIT": depth_limit,
            "CLOSESPIDER_PAGECOUNT": close_pagecount,
            "CONCURRENT_REQUESTS": config.concurrency,
            "DOWNLOAD_TIMEOUT": config.timeout,
            "DOWNLOAD_DELAY": config.download_delay,
            "RETRY_ENABLED": True,
            "RETRY_TIMES": config.retry_times,
            "ROBOTSTXT_OBEY": True,
            "LOG_ENABLED": False,
            "USER_AGENT": "OpenViking/0.4 (+recursive-web-crawler)",
            "DOWNLOADER_MIDDLEWARES": {
                "openviking.parse.accessors.web_crawler.middlewares."
                "RequestValidatorMiddleware": 50,
            },
        }
    )


class ScrapyWebCrawler:
    def __init__(self, config: CrawlConfig) -> None:
        self.config = config

    async def crawl(self, root_url: str) -> CrawlResult:
        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_run_crawl_worker,
            args=(root_url, self.config, result_queue),
        )
        process.start()
        pages, downloads, error = await self._wait_worker_result(process, result_queue)
        if error:
            raise RuntimeError(error)
        if process.exitcode not in (0, None):
            raise RuntimeError(f"Scrapy crawler exited with code {process.exitcode}.")
        return self._build_result(pages, downloads)

    @staticmethod
    async def _wait_worker_result(
        process,
        result_queue,
    ) -> tuple[list[CrawledPage], list[CrawledDownload], str | None]:
        while True:
            try:
                pages, downloads, error = await asyncio.to_thread(result_queue.get, True, 0.2)
                await asyncio.to_thread(process.join)
                return pages, downloads, error
            except queue.Empty:
                if process.is_alive():
                    continue
                await asyncio.to_thread(process.join)
                return ScrapyWebCrawler._read_worker_result(result_queue)

    @staticmethod
    def _read_worker_result(
        result_queue,
    ) -> tuple[list[CrawledPage], list[CrawledDownload], str | None]:
        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return [], [], "Scrapy crawler did not return a result."

    @staticmethod
    def _build_result(pages: list[CrawledPage], downloads: list[CrawledDownload]) -> CrawlResult:
        result = CrawlResult(pages=pages, downloads=downloads)
        result.total_downloads = len(downloads)
        for page in pages:
            if page.status == "success":
                result.total_crawled += 1
            elif page.status == "skipped":
                result.total_skipped += 1
            else:
                result.total_failed += 1
            if page.source == "playwright":
                result.fallback_rendered += 1
        return result
