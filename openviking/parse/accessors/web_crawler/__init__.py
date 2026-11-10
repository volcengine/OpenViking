# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTML crawler internals used by HTTP web imports."""

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.models import CrawledDownload, CrawledPage, CrawlResult
from openviking.parse.accessors.web_crawler.web_crawler import ScrapyWebCrawler

__all__ = [
    "CrawlConfig",
    "CrawledDownload",
    "CrawledPage",
    "CrawlResult",
    "ScrapyWebCrawler",
]
