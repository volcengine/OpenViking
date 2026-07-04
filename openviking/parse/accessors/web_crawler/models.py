# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared data models for recursive HTML crawling."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CrawledPage:
    url: str = ""
    final_url: str = ""
    depth: int = 0
    status: str = "success"
    html: Optional[str] = None
    source: str = "scrapy_static"
    error: Optional[str] = None


@dataclass
class CrawledDownload:
    url: str = ""
    depth: int = 0


@dataclass
class CrawlResult:
    pages: list[CrawledPage] = field(default_factory=list)
    downloads: list[CrawledDownload] = field(default_factory=list)
    total_crawled: int = 0
    total_downloads: int = 0
    total_skipped: int = 0
    total_failed: int = 0
    fallback_rendered: int = 0
