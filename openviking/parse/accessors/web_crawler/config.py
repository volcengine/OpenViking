# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Configuration for recursive HTML crawling."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional


@dataclass
class CrawlConfig:
    depth: int = 0
    max_pages: int = 50
    include_paths: Optional[list[str]] = None
    exclude_paths: Optional[list[str]] = None
    allow_external_links: bool = False
    skip_download_links: bool = True

    concurrency: int = 10
    timeout: float = 10.0
    download_delay: float = 0.2
    retry_times: int = 2
    max_links_per_page: int = 500
    max_html_bytes: int = 10 * 1024 * 1024
    fallback_playwright: bool = True
    playwright_timeout: float = 30.0
    request_validator: Optional[Callable[[str], None]] = None

    def __post_init__(self) -> None:
        if self.depth < -1:
            raise ValueError("depth must be >= -1 (use -1 for unlimited) for recursive web crawling.")
        if self.max_pages < 1 and self.max_pages != -1:
            raise ValueError("max_pages must be -1 (unlimited) or >= 1 for recursive web crawling.")
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1 for recursive web crawling.")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0 for recursive web crawling.")
        if self.download_delay < 0:
            raise ValueError("download_delay must be >= 0 for recursive web crawling.")
        if self.retry_times < 0:
            raise ValueError("retry_times must be >= 0 for recursive web crawling.")
        if self.max_links_per_page < 1:
            raise ValueError("max_links_per_page must be >= 1.")
        if self.max_html_bytes < 1:
            raise ValueError("max_html_bytes must be >= 1.")
        if self.playwright_timeout <= 0:
            raise ValueError("playwright_timeout must be > 0 for recursive web crawling.")
