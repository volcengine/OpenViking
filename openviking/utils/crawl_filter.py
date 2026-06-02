# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""URL filter pipeline for web crawling."""

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import List, Optional, Set
from urllib.parse import urlparse

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FilterStats:
    rejected_by_scheme: int = 0
    rejected_by_static: int = 0
    rejected_by_domain: int = 0
    rejected_by_path: int = 0
    rejected_by_dedup: int = 0
    rejected_by_limit: int = 0
    rejected_by_content_type: int = 0


@dataclass
class FilterResult:
    accepted: List[str] = field(default_factory=list)
    stats: FilterStats = field(default_factory=FilterStats)


@dataclass
class CrawlConfig:
    depth: int = 0
    max_pages: int = 100
    max_links_per_page: int = 50
    allow_external_links: bool = False
    include_paths: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None
    concurrency: int = 5
    timeout: float = 10.0
    user_agent: Optional[str] = None
    use_playwright: bool = True


class CrawlFilter:
    INVALID_SCHEMES = frozenset(
        {
            "mailto:",
            "javascript:",
            "tel:",
            "ftp:",
            "ftps:",
            "data:",
            "file:",
            "blob:",
            "about:",
        }
    )

    STATIC_EXTENSIONS = frozenset(
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
            ".otf",
            ".mp3",
            ".mp4",
            ".avi",
            ".mov",
            ".wmv",
            ".flv",
            ".webm",
            ".wav",
            ".ogg",
            ".pdf",
            ".zip",
            ".tar",
            ".gz",
            ".rar",
            ".7z",
            ".bz2",
            ".rss",
            ".atom",
            ".xml",
            ".json",
            ".yaml",
            ".yml",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".exe",
            ".dmg",
            ".iso",
            ".img",
        }
    )

    HTML_EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".asp", ".aspx", ".php", ".jsp", ".cgi"})

    NON_HTML_CONTENT_TYPES = frozenset(
        {
            "application/pdf",
            "application/zip",
            "application/x-tar",
            "application/gzip",
            "application/x-rar-compressed",
            "application/x-7z-compressed",
            "application/x-bzip2",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.ms-powerpoint",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "application/octet-stream",
            "application/javascript",
            "application/json",
            "application/xml",
            "application/rss+xml",
            "application/atom+xml",
            "application/x-shockwave-flash",
            "audio/mpeg",
            "audio/ogg",
            "audio/wav",
            "audio/webm",
            "video/mp4",
            "video/webm",
            "video/avi",
            "video/x-msvideo",
            "video/quicktime",
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/svg+xml",
            "image/webp",
            "image/bmp",
            "image/x-icon",
            "image/avif",
            "font/woff",
            "font/woff2",
            "font/ttf",
            "font/otf",
            "text/css",
        }
    )

    def __init__(self, config: CrawlConfig) -> None:
        self.config = config
        self._visited: Set[str] = set()

    def filter(self, urls: List[str], base_url: str) -> FilterResult:
        stats = FilterStats()
        accepted: List[str] = []

        for url in urls:
            if not self._filter_scheme(url):
                stats.rejected_by_scheme += 1
                continue

            if not self._filter_static(url):
                stats.rejected_by_static += 1
                continue

            if not self._filter_domain(url, base_url):
                stats.rejected_by_domain += 1
                continue

            if not self._filter_path(url):
                stats.rejected_by_path += 1
                continue

            normalized = self.normalize_url(url)
            if normalized in self._visited:
                stats.rejected_by_dedup += 1
                continue
            self._visited.add(normalized)

            if len(accepted) >= self.config.max_links_per_page:
                stats.rejected_by_limit += 1
                continue

            accepted.append(url)

        return FilterResult(accepted=accepted, stats=stats)

    def _filter_scheme(self, url: str) -> bool:
        lower = url.lower()
        for scheme in self.INVALID_SCHEMES:
            if lower.startswith(scheme):
                return False
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https")

    def _filter_static(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        ext = os.path.splitext(path)[1]
        return ext not in self.STATIC_EXTENSIONS

    def _filter_domain(self, url: str, base_url: str) -> bool:
        if self.config.allow_external_links:
            return True
        base_netloc = urlparse(base_url).netloc.lower()
        target_netloc = urlparse(url).netloc.lower()
        return target_netloc == base_netloc

    def _filter_path(self, url: str) -> bool:
        path = urlparse(url).path

        if self.config.exclude_paths:
            for pattern in self.config.exclude_paths:
                clean_pattern = pattern.strip("/")
                if (
                    fnmatch(path, f"*{clean_pattern}*")
                    or fnmatch(path, pattern)
                ):
                    return False

        if self.config.include_paths:
            matched = False
            for pattern in self.config.include_paths:
                clean_pattern = pattern.strip("/")
                if (
                    fnmatch(path, f"*{clean_pattern}*")
                    or fnmatch(path, pattern)
                ):
                    matched = True
                    break
            if not matched:
                return False

        return True

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url)
        normalized = (
            f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        )
        normalized = normalized.rstrip("/") or normalized
        if parsed.query:
            params = sorted(parsed.query.split("&"))
            normalized += "?" + "&".join(params)
        return normalized

    def is_global_limit_reached(self, pages_crawled: int) -> bool:
        return self.config.max_pages > 0 and pages_crawled >= self.config.max_pages

    def add_visited(self, url: str) -> None:
        self._visited.add(self.normalize_url(url))

    def filter_by_content_type(
        self, url: str, content_type: Optional[str] = None
    ) -> bool:
        if not content_type:
            return True

        ct = content_type.lower().strip().split(";")[0].strip()

        if ct in self.NON_HTML_CONTENT_TYPES:
            logger.debug(f"[ContentTypeFilter] rejected {url} (content-type: {ct})")
            return False

        if ct.startswith("image/") or ct.startswith("video/") or ct.startswith("audio/"):
            logger.debug(f"[ContentTypeFilter] rejected {url} (media type: {ct})")
            return False
        if ct.startswith("font/") or ct == "text/css":
            logger.debug(f"[ContentTypeFilter] rejected {url} (asset type: {ct})")
            return False

        return True
