# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Extract <a href> links from HTML content."""

from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LinkExtractResult:
    urls: List[str] = None
    total_links_found: int = 0

    def __post_init__(self):
        if self.urls is None:
            self.urls = []


class LinkExtractor:
    def extract(self, html_content: str, base_url: str) -> LinkExtractResult:
        soup = BeautifulSoup(html_content, "html.parser")
        raw_hrefs: List[str] = []
        for tag in soup.find_all("a", href=True):
            href = tag.get("href", "").strip()
            if href:
                raw_hrefs.append(href)

        absolute_urls: List[str] = []
        for href in raw_hrefs:
            resolved = self._resolve_url(href, base_url)
            if resolved:
                absolute_urls.append(resolved)

        return LinkExtractResult(
            urls=absolute_urls,
            total_links_found=len(raw_hrefs),
        )

    def _resolve_url(self, href: str, base_url: str) -> Optional[str]:
        if href.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return None
        try:
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                return None
            return absolute
        except Exception:
            return None
