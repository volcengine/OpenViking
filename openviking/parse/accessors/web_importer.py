# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Import ordinary web pages as a temporary local HTML directory."""

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlsplit, urlunsplit

from openviking.parse.accessors.http_accessor import HTTPAccessor
from openviking.parse.accessors.web_crawler import CrawlConfig, ScrapyWebCrawler
from openviking.parse.accessors.web_crawler.playwright_renderer import (
    PLAYWRIGHT_CHROMIUM_INSTALL_HINT,
    PLAYWRIGHT_PACKAGE_INSTALL_HINT,
)
from openviking.parse.accessors.web_feed_accessor import (
    FeedEntry,
    _dedup_relpath,
    _entry_to_relpath,
    _sanitize_filename,
    url_to_relpath,
)
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

DEPTH_UNLIMITED = -1
MAX_PAGES_UNLIMITED = -1

# Actionable install hints the renderer records on CrawledPage.error when the
# Playwright fallback is needed but unavailable. These are surfaced to the user
# even for non-entry pages, which would otherwise only show up as a failure
# count.
_RENDER_INSTALL_HINTS = frozenset(
    {
        PLAYWRIGHT_PACKAGE_INSTALL_HINT,
        PLAYWRIGHT_CHROMIUM_INSTALL_HINT,
    }
)


@dataclass(frozen=True)
class WebImportOptions:
    depth: int = 0
    max_pages: int = 50
    include_paths: Optional[list[str]] = None
    exclude_paths: Optional[list[str]] = None
    allow_external_links: bool = False
    skip_download_links: bool = True


@dataclass(frozen=True)
class WebImportResult:
    path: Path
    meta: Dict[str, Any]


def parse_web_import_options(kwargs: Dict[str, Any]) -> WebImportOptions:
    """Pop web importer options from accessor kwargs."""
    depth = _pop_int_arg(kwargs, "depth", 0, min_value=-1)
    max_pages = _pop_int_arg(kwargs, "max_pages", 50, min_value=-1)
    if max_pages == 0:
        raise InvalidArgumentError("args.max_pages must be -1 (unlimited) or >= 1.")
    allow_external_links = _pop_bool_arg(kwargs, "allow_external_links", False)
    skip_download_links = _pop_bool_arg(kwargs, "skip_download_links", True)
    if (
        depth == DEPTH_UNLIMITED
        and max_pages == MAX_PAGES_UNLIMITED
        and allow_external_links
    ):
        raise InvalidArgumentError(
            "args.depth=-1 and args.max_pages=-1 cannot be combined with "
            "allow_external_links=true; at least one bound is required when "
            "crawling external sites."
        )
    return WebImportOptions(
        depth=depth,
        max_pages=max_pages,
        include_paths=_pop_optional_patterns(kwargs, "include_paths"),
        exclude_paths=_pop_optional_patterns(kwargs, "exclude_paths"),
        allow_external_links=allow_external_links,
        skip_download_links=skip_download_links,
    )


class WebImporter:
    """Crawl ordinary web pages and materialize successful pages as HTML files."""

    async def import_to_directory(
        self,
        *,
        root_url: str,
        options: WebImportOptions,
        request_validator=None,
    ) -> WebImportResult:
        config = CrawlConfig(
            depth=options.depth,
            max_pages=options.max_pages,
            include_paths=options.include_paths,
            exclude_paths=options.exclude_paths,
            allow_external_links=options.allow_external_links,
            skip_download_links=options.skip_download_links,
            request_validator=request_validator,
        )
        crawl_result = await ScrapyWebCrawler(config).crawl(root_url)
        success_pages = self._dedupe_success_pages(crawl_result.pages)
        if not any(page.depth == 0 for page in success_pages):
            detail = self._entry_failure_detail(crawl_result.pages)
            message = f"Failed to fetch entry page: {root_url}"
            if detail:
                message = f"{message} ({detail})"
            raise RuntimeError(message)

        render_hints = self._render_install_hints(crawl_result.pages)
        if render_hints:
            logger.warning(
                "Some pages could not be rendered during web import of %s and were "
                "skipped: %s",
                root_url,
                " ".join(render_hints),
            )

        temp_root = Path(tempfile.mkdtemp(prefix="ov_web_"))
        temp_dir = temp_root / _host_name(root_url)
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            used_relpaths: set[str] = set()
            for page in success_pages:
                page_url = page.final_url or page.url
                relpath = self._page_relpath(page)
                relpath = _dedup_relpath(relpath, _normalize_page_url(page_url), used_relpaths)
                used_relpaths.add(relpath)
                dest = self._safe_dest(temp_dir, relpath)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(page.html or "", encoding="utf-8")
            downloaded_files = await self._write_downloads(
                crawl_result.downloads,
                temp_dir,
                used_relpaths,
                request_validator=request_validator,
            )
        except Exception:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise

        return WebImportResult(
            path=temp_dir,
            meta={
                "web_import": True,
                "page_count": len(success_pages),
                "download_count": len(downloaded_files),
                "crawl_result": _crawl_summary(crawl_result),
                "render_hints": render_hints,
                "original_filename": _host_name(root_url),
            },
        )

    @staticmethod
    def _render_install_hints(pages) -> list[str]:
        """Collect actionable render-install hints from failed pages (any depth).

        Child SPA pages that fail because Playwright is unavailable only record
        the hint on ``CrawledPage.error``; the entry page still succeeds, so the
        caller would otherwise see the hint nowhere. Surface each distinct hint
        once so the user can act on it.
        """
        hints: list[str] = []
        for page in pages:
            if page.status == "success" or not page.error:
                continue
            if page.error in _RENDER_INSTALL_HINTS and page.error not in hints:
                hints.append(page.error)
        return hints

    @staticmethod
    def _entry_failure_detail(pages) -> str:
        """Return the failure reason of the entry page, if one was recorded.

        The renderer surfaces actionable hints (e.g. missing Playwright
        install) via ``CrawledPage.error``; without this the caller only sees
        the generic "Failed to fetch entry page" message.
        """
        for page in pages:
            if page.depth == 0 and page.status != "success" and page.error:
                return page.error
        return ""

    @staticmethod
    def _dedupe_success_pages(pages) -> list[Any]:
        seen: set[str] = set()
        deduped: list[Any] = []
        for page in pages:
            if page.status != "success":
                continue
            key = _normalize_page_url(page.final_url or page.url)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(page)
        return deduped

    @staticmethod
    def _safe_dest(root: Path, relpath: str) -> Path:
        dest = (root / relpath).resolve()
        root_resolved = root.resolve()
        try:
            dest.relative_to(root_resolved)
        except ValueError as exc:
            digest = hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:8]
            dest = root_resolved / f"page-{digest}.html"
        return dest

    @staticmethod
    def _page_relpath(page: Any) -> str:
        page_url = page.final_url or page.url
        title = _extract_page_title(page.html or "")
        if title:
            return _entry_to_relpath(FeedEntry(url=page_url, title=title))
        return url_to_relpath(page_url)

    async def _write_downloads(
        self,
        downloads: list[Any],
        temp_dir: Path,
        used_relpaths: set[str],
        *,
        request_validator=None,
    ) -> list[str]:
        http_accessor = HTTPAccessor()
        written: list[str] = []
        for download in self._dedupe_downloads(downloads):
            temp_path, _url_type, meta = await http_accessor._download_url(
                download.url,
                request_validator=request_validator,
            )
            try:
                relpath = _download_relpath(download.url, meta)
                relpath = _dedup_relpath(relpath, _normalize_page_url(download.url), used_relpaths)
                used_relpaths.add(relpath)
                dest = self._safe_dest(temp_dir, relpath)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(temp_path, dest)
                written.append(relpath)
            finally:
                Path(temp_path).unlink(missing_ok=True)
        return written

    @staticmethod
    def _dedupe_downloads(downloads: list[Any]) -> list[Any]:
        seen: set[str] = set()
        deduped: list[Any] = []
        for download in downloads:
            key = _normalize_page_url(download.url)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(download)
        return deduped


def _crawl_summary(crawl_result: Any) -> Dict[str, Any]:
    return {
        "total_crawled": crawl_result.total_crawled,
        "total_downloads": getattr(crawl_result, "total_downloads", 0),
        "total_failed": crawl_result.total_failed,
        "total_skipped": crawl_result.total_skipped,
        "fallback_rendered": crawl_result.fallback_rendered,
    }


def _normalize_page_url(url: str) -> str:
    parts = urlsplit(url or "")
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower().rstrip(".")
    port = parts.port
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port = None
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _host_name(url: str) -> str:
    host = urlsplit(url).hostname or "web"
    return host.rstrip(".").lower() or "web"


def _download_relpath(url: str, meta: Dict[str, Any]) -> str:
    parts = urlsplit(url)
    segments = [unquote(segment) for segment in parts.path.split("/") if segment]
    original_filename = meta.get("original_filename") or "download"
    filename = _sanitize_filename(str(original_filename)) or "download"
    if segments:
        parents = [_sanitize_filename(segment) for segment in segments[:-1]]
        parents = [segment for segment in parents if segment]
        return "/".join(parents + [filename])
    return filename


def _extract_page_title(html: str) -> Optional[str]:
    """Extract a readable page title for local filename selection.

    This deliberately stays lightweight and does not use trafilatura; full
    content/title extraction remains the HTMLParser's responsibility.
    """
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for selector in ("h1", "title"):
            node = soup.select_one(selector)
            if node:
                title = _clean_title(node.get_text(" ", strip=True))
                if title:
                    return title
    except Exception:
        pass
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if match:
        title = re.sub(r"<[^>]+>", "", match.group(1))
        return _clean_title(title)
    return None


def _clean_title(title: str) -> Optional[str]:
    title = re.sub(r"\s+", " ", title or "").strip()
    return title or None


def _pop_int_arg(
    args: Dict[str, Any],
    name: str,
    default: int,
    *,
    min_value: Optional[int] = None,
) -> int:
    value = args.pop(name, None)
    if value in (None, ""):
        value = default
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise InvalidArgumentError(f"args.{name} must be an integer.") from exc
    if min_value is not None and value < min_value:
        raise InvalidArgumentError(f"args.{name} must be >= {min_value}.")
    return value


def _pop_bool_arg(args: Dict[str, Any], name: str, default: bool) -> bool:
    value = args.pop(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise InvalidArgumentError(f"args.{name} must be a boolean.")


def _pop_optional_patterns(args: Dict[str, Any], name: str) -> Optional[list[str]]:
    value = args.pop(name, None)
    if value is None or value == "":
        return None
    if isinstance(value, str):
        patterns = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        patterns = [str(part).strip() for part in value]
    else:
        raise InvalidArgumentError(f"args.{name} must be a string or list of strings.")
    return [pattern for pattern in patterns if pattern] or None
