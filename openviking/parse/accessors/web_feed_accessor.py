# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Web Feed Accessor.

Ingests an ENTIRE website in one shot from a single "collection" URL:
- XML sitemaps (``<urlset>``) and sitemap indexes (``<sitemapindex>``, recursed)
- RSS 2.0 feeds (``<rss><channel><item>``)
- Atom feeds (``<feed><entry>``)

The shared core capability is "a URL that enumerates many page URLs". Whatever
the source, this accessor mirrors the listed pages into a single local temp
directory and returns it as a directory ``LocalResource`` — exactly the contract
GitAccessor uses. The downstream DirectoryParser then builds ONE resource tree
(one ``viking://resources/<host>`` URI, one watchable node) with a child per page.

Because a watch re-runs ``add_resource(path=<feed url>)`` on each refresh, watching
a sitemap/feed URL keeps the whole site up to date: new pages appear and removed
pages drop automatically on the next rebuild.

This module also exposes ``discover_feed_hint``: a cheap, failure-tolerant probe
used when a user adds a single webpage, to *suggest* whole-site ingestion without
ever auto-crawling.
"""

import asyncio
import fnmatch
import hashlib
import html as html_lib
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import unquote, urljoin, urlparse
from urllib.robotparser import RobotFileParser

from openviking.parse.base import lazy_import
from openviking.utils.network_guard import build_httpx_request_validation_hooks
from openviking_cli.utils.logger import get_logger

from .base import DataAccessor, LocalResource, SourceType
from .http_accessor import HTTPAccessor

logger = get_logger(__name__)

_USER_AGENT = HTTPAccessor.DEFAULT_USER_AGENT

# kwargs keys that explicitly force / forbid whole-site ingestion (args={"site": ...}).
_OVERRIDE_KEYS = ("site", "sitemap", "feed", "as_site")

# URL path basenames that confidently indicate a sitemap / feed.
_FEED_BASENAMES = {
    "rss.xml",
    "feed.xml",
    "atom.xml",
    "feed.atom",
    "index.xml",
    "rss",
    "feed",
    "feeds",
    "atom",
}

# Web page extensions that should be stripped from a URL slug (we re-save as .html).
_WEB_PAGE_EXTENSIONS = (".html", ".htm", ".php", ".asp", ".aspx", ".jsp")

# Common conventional locations for a site's sitemap / feed, probed when auto-discovering.
_COMMON_FEED_PATHS = (
    "/sitemap.xml",
    "/sitemap-index.xml",
    "/sitemap_index.xml",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/index.xml",
    "/feed",
    "/rss",
)


@dataclass
class FeedEntry:
    """A single page discovered in a sitemap or feed."""

    url: str
    title: Optional[str] = None
    lastmod: Optional[str] = None
    inline_html: Optional[str] = None
    """Full article HTML carried inline by the feed (RSS content:encoded / Atom
    content). When present we save it directly instead of fetching the page."""


# --------------------------------------------------------------------------- #
# Module-level helpers (kept free-standing so they're easy to unit test)
# --------------------------------------------------------------------------- #
def _localname(tag: str) -> str:
    """Return the namespace-stripped, lowercased local name of an XML tag."""
    return tag.rsplit("}", 1)[-1].lower()


def looks_like_feed_url(source: Union[str, Path]) -> bool:
    """Heuristic: does this URL path look like a sitemap / RSS / Atom feed?

    Conservative on purpose — ordinary article URLs must fall through to
    HTTPAccessor for single-page ingestion.
    """
    source_str = str(source)
    if not source_str.startswith(("http://", "https://")):
        return False
    basename = urlparse(source_str).path.lower().rstrip("/").rsplit("/", 1)[-1]
    if not basename:
        return False
    if basename.startswith("sitemap") and basename.endswith(".xml"):
        return True
    if basename in _FEED_BASENAMES:
        return True
    if basename.endswith((".rss", ".atom")):
        return True
    return False


def _resolve_override(kwargs: Dict[str, Any]) -> Optional[bool]:
    """Resolve an explicit site/feed override from kwargs, if any."""
    for key in _OVERRIDE_KEYS:
        value = kwargs.get(key)
        if isinstance(value, bool):
            return value
    return None


def _sanitize_filename(name: str, max_len: int = 120) -> str:
    """Make a string safe to use as a single path segment.

    Keeps unicode (e.g. CJK titles) readable; only strips path separators,
    reserved/control characters, and trims length. The resulting stem becomes
    the page node's title downstream, so readability matters.
    """
    name = (name or "").strip()
    # Replace path separators, control chars, and filesystem-reserved chars.
    name = re.sub(r'[\\/\x00-\x1f<>:"|?*]+', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    if len(name) > max_len:
        name = name[:max_len].strip(" .-")
    return name


def _slugify_segment(segment: str) -> str:
    """Turn one URL path segment into a clean filename stem (no extension)."""
    segment = unquote(segment)
    lower = segment.lower()
    for ext in _WEB_PAGE_EXTENSIONS:
        if lower.endswith(ext):
            segment = segment[: -len(ext)]
            break
    return _sanitize_filename(segment)


def url_to_relpath(url: str) -> str:
    """Map a page URL to a relative mirror path, preserving directory structure.

    Examples:
        https://h/                       -> index.html
        https://h/a/b                    -> a/b.html
        https://h/a/b/                    -> a/b.html
        https://h/post/foo/index.html    -> post/foo/index.html
    """
    segments = [s for s in urlparse(url).path.split("/") if s]
    if not segments:
        return "index.html"
    *parents, leaf = segments
    leaf_stem = _slugify_segment(leaf) or "index"
    parent_parts = [p for p in (_sanitize_filename(unquote(s)) for s in parents) if p]
    return "/".join(parent_parts + [f"{leaf_stem}.html"])


def _entry_to_relpath(entry: FeedEntry) -> str:
    """Choose the mirror path for an entry.

    Feed entries usually carry a real title -> use it as a flat, readable
    filename. Sitemap entries have no title -> mirror the URL path structure.
    """
    if entry.title:
        stem = _sanitize_filename(entry.title)
        if stem:
            return f"{stem}.html"
    return url_to_relpath(entry.url)


def _dedup_relpath(relpath: str, url: str, used: set) -> str:
    """Disambiguate a colliding relpath with a short stable hash of the URL."""
    if relpath not in used:
        return relpath
    p = PurePosixPath(relpath)
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:6]
    newname = f"{p.stem}-{digest}{p.suffix}"
    parent = str(p.parent)
    return newname if parent == "." else f"{parent}/{newname}"


def _wrap_inline_html(title: Optional[str], fragment: str) -> str:
    """Wrap an inline feed HTML fragment into a minimal standalone document."""
    title_tag = f"<title>{html_lib.escape(title)}</title>" if title else ""
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"{title_tag}</head><body>{fragment}</body></html>"
    )


def _split_patterns(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [p.strip() for p in str(value).split(",") if p.strip()]


def _match_filters(url: str, include: Optional[str], exclude: Optional[str]) -> bool:
    """Apply comma-separated include/exclude glob (or substring) filters to a URL."""
    for pat in _split_patterns(exclude):
        if fnmatch.fnmatch(url, pat) or pat in url:
            return False
    includes = _split_patterns(include)
    if includes:
        return any(fnmatch.fnmatch(url, pat) or pat in url for pat in includes)
    return True


def _build_httpx_client_kwargs(request_validator, timeout: float) -> Dict[str, Any]:
    """Build httpx.AsyncClient kwargs with the shared SSRF network guard.

    Mirrors HTTPAccessor's client construction so sitemaps, feeds, sub-sitemaps,
    robots.txt and every mirrored page go through the same request validation.
    """
    client_kwargs: Dict[str, Any] = {"timeout": timeout, "follow_redirects": True}
    event_hooks = build_httpx_request_validation_hooks(request_validator)
    if event_hooks:
        client_kwargs["event_hooks"] = event_hooks
        client_kwargs["trust_env"] = False
    return client_kwargs


def _load_webfeed_config():
    """Return the configured WebFeedConfig, falling back to defaults."""
    from openviking_cli.utils.config.parser_config import WebFeedConfig

    try:
        from openviking_cli.utils.config import get_openviking_config

        cfg = getattr(get_openviking_config(), "webfeed", None)
        if cfg is not None:
            return cfg
    except Exception:
        pass
    return WebFeedConfig()


# --------------------------------------------------------------------------- #
# Accessor
# --------------------------------------------------------------------------- #
class WebFeedAccessor(DataAccessor):
    """Accessor that ingests a whole website from a sitemap / RSS / Atom URL."""

    PRIORITY = 60  # Feishu 100 > Git 80 > WebFeed 60 > HTTP 50 > Local 1

    @property
    def priority(self) -> int:
        return self.PRIORITY

    def can_handle(self, source: Union[str, Path], **kwargs) -> bool:
        """Handle sitemap/feed URLs (by heuristic, or by explicit override).

        ``args={"site": True}`` forces any http(s) URL through this accessor;
        ``args={"site": False}`` opts a feed-looking URL back out to HTTPAccessor.
        """
        source_str = str(source)
        if not source_str.startswith(("http://", "https://")):
            return False
        override = _resolve_override(kwargs)
        if override is not None:
            return override
        return looks_like_feed_url(source_str)

    async def access(self, source: Union[str, Path], **kwargs) -> LocalResource:
        """Fetch the sitemap/feed, mirror its pages, return a directory resource."""
        source_url = str(source)
        request_validator = kwargs.get("request_validator")
        cfg = self._resolve_settings(kwargs)
        include = kwargs.get("include")
        exclude = kwargs.get("exclude")

        temp_dir = await asyncio.to_thread(tempfile.mkdtemp, prefix="ov_webfeed_")
        try:
            client_kwargs = _build_httpx_client_kwargs(request_validator, cfg["request_timeout"])
            httpx = lazy_import("httpx")
            async with httpx.AsyncClient(**client_kwargs) as client:
                # The given URL may already be a sitemap/feed, or it may be a plain
                # page / bare domain (e.g. args={"site": True} on "https://t0saki.com").
                # In the latter case, auto-discover the site's sitemap/RSS.
                feed_url, prefetched = await self._resolve_feed_source(source_url, client)
                entries, feed_kind = await self._collect(
                    feed_url, client, cfg, prefetched=prefetched
                )
                if not entries:
                    raise RuntimeError(f"No pages found in sitemap/feed: {feed_url}")

                entries = self._filter_entries(entries, feed_url, cfg, include, exclude)
                entries = await self._apply_robots(entries, feed_url, client, cfg)

                if len(entries) > cfg["max_pages"]:
                    logger.warning(
                        "[WebFeedAccessor] %s lists %d pages; truncating to max_pages=%d",
                        feed_url,
                        len(entries),
                        cfg["max_pages"],
                    )
                    entries = entries[: cfg["max_pages"]]

                if not entries:
                    raise RuntimeError(f"No ingestible pages remained after filtering: {feed_url}")

                pages = await self._mirror(entries, client, temp_dir, cfg)

            if not pages:
                raise RuntimeError(f"Failed to fetch any page from: {feed_url}")

            host = urlparse(feed_url).netloc
            meta = {
                # Drives the root URI -> viking://resources/<host>
                "original_filename": host,
                "feed_url": feed_url,
                "feed_kind": feed_kind,
                "url_type": feed_kind,
                "page_count": len(pages),
                "pages": pages,
            }
            logger.info(
                "[WebFeedAccessor] Mirrored %d page(s) from %s (%s)",
                len(pages),
                feed_url,
                feed_kind,
            )
            return LocalResource(
                path=Path(temp_dir),
                source_type=SourceType.HTTP,
                original_source=feed_url,
                meta=meta,
                is_temporary=True,
            )
        except Exception as e:
            logger.error("[WebFeedAccessor] Failed to access %s: %s", source_url, e, exc_info=True)
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    # -- settings ----------------------------------------------------------- #
    def _resolve_settings(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Merge WebFeedConfig defaults with per-call kwargs overrides."""
        cfg = _load_webfeed_config()

        def pick(key, cast, default):
            value = kwargs.get(key)
            if value is None:
                value = getattr(cfg, key, default)
            try:
                return cast(value)
            except (TypeError, ValueError):
                return default

        return {
            "max_pages": pick("max_pages", int, 200),
            "max_concurrency": max(1, pick("max_concurrency", int, 5)),
            "request_timeout": pick("request_timeout", float, 30.0),
            "politeness_delay": max(0.0, pick("politeness_delay", float, 0.2)),
            "same_host_only": bool(
                kwargs.get("same_host_only", getattr(cfg, "same_host_only", True))
            ),
            "respect_robots": bool(
                kwargs.get("respect_robots", getattr(cfg, "respect_robots", True))
            ),
            "max_depth": max(1, pick("max_depth", int, 2)),
        }

    # -- discovery ---------------------------------------------------------- #
    async def _fetch_bytes(self, url: str, client) -> bytes:
        response = await client.get(url, headers={"User-Agent": _USER_AGENT})
        response.raise_for_status()
        return response.content

    def _sniff(self, content: bytes) -> Tuple[str, Any]:
        """Classify a fetched document by its XML root element."""
        from defusedxml.ElementTree import fromstring

        try:
            root = fromstring(content)
        except Exception:
            # Not well-formed XML we can route — let feedparser try as a feed.
            return "feed", None
        name = _localname(root.tag)
        if name == "urlset":
            return "urlset", root
        if name == "sitemapindex":
            return "sitemapindex", root
        if name in ("rss", "feed"):
            return "feed", root
        return "unknown", root

    def _is_feed_document(self, content: bytes) -> bool:
        """Whether ``content`` is a usable sitemap / sitemapindex / RSS / Atom doc."""
        kind, _ = self._sniff(content)
        if kind in ("urlset", "sitemapindex"):
            return True
        if kind == "feed":
            # "feed" also covers the non-XML fallback; confirm real entries exist.
            try:
                import feedparser

                return len(feedparser.parse(content).entries) > 0
            except Exception:
                return False
        return False

    async def _resolve_feed_source(self, url: str, client) -> Tuple[str, bytes]:
        """Return a ``(feed_url, content)`` that is an actual sitemap/feed.

        If ``url`` already points at a sitemap/feed, it is returned as-is. Otherwise
        (a plain page or bare domain — e.g. ``args={"site": True}`` on a homepage),
        auto-discover the site's sitemap/RSS via robots.txt, ``<link rel=alternate>``
        autodiscovery, and conventional locations, and return the first real one.
        """
        content = await self._fetch_bytes(url, client)
        if self._is_feed_document(content):
            return url, content

        for candidate in await _gather_feed_candidates(client, url, content):
            if candidate == url:
                continue
            try:
                cand_content = await self._fetch_bytes(candidate, client)
            except Exception:
                continue
            if self._is_feed_document(cand_content):
                logger.info("[WebFeedAccessor] auto-discovered feed %s for %s", candidate, url)
                return candidate, cand_content

        raise RuntimeError(
            f"{url} is not a sitemap/feed and no sitemap/RSS could be auto-discovered "
            "for the site. Pass the sitemap/feed URL directly."
        )

    async def _collect(
        self,
        url: str,
        client,
        cfg: Dict[str, Any],
        depth: int = 0,
        visited: Optional[set] = None,
        prefetched: Optional[bytes] = None,
    ) -> Tuple[List[FeedEntry], str]:
        """Recursively collect entries from a sitemap / sitemapindex / feed."""
        if visited is None:
            visited = {url}
        content = prefetched if prefetched is not None else await self._fetch_bytes(url, client)
        kind, root = self._sniff(content)

        if kind == "sitemapindex":
            if depth >= cfg["max_depth"]:
                logger.warning(
                    "[WebFeedAccessor] sitemapindex depth limit (%d) reached at %s",
                    cfg["max_depth"],
                    url,
                )
                return [], "sitemap"
            all_entries: List[FeedEntry] = []
            for sub_url in self._parse_sitemapindex(root):
                if sub_url in visited:
                    continue
                visited.add(sub_url)
                try:
                    sub_entries, _ = await self._collect(sub_url, client, cfg, depth + 1, visited)
                    all_entries.extend(sub_entries)
                except Exception as e:
                    logger.warning(
                        "[WebFeedAccessor] Failed to read sub-sitemap %s: %s", sub_url, e
                    )
            return all_entries, "sitemap"

        if kind == "urlset":
            return self._parse_urlset(root), "sitemap"

        if kind == "feed":
            return self._parse_feed(content, base_url=url)

        raise RuntimeError(f"Unrecognized document (not a sitemap or feed): {url}")

    @staticmethod
    def _parse_urlset(root) -> List[FeedEntry]:
        entries: List[FeedEntry] = []
        for url_el in root:
            if _localname(url_el.tag) != "url":
                continue
            loc = None
            lastmod = None
            for child in url_el:
                ln = _localname(child.tag)
                if ln == "loc":
                    loc = (child.text or "").strip()
                elif ln == "lastmod":
                    lastmod = (child.text or "").strip()
            if loc:
                entries.append(FeedEntry(url=loc, lastmod=lastmod))
        return entries

    @staticmethod
    def _parse_sitemapindex(root) -> List[str]:
        locs: List[str] = []
        for sm_el in root:
            if _localname(sm_el.tag) != "sitemap":
                continue
            for child in sm_el:
                if _localname(child.tag) == "loc":
                    loc = (child.text or "").strip()
                    if loc:
                        locs.append(loc)
        return locs

    @staticmethod
    def _parse_feed(content: bytes, base_url: str) -> Tuple[List[FeedEntry], str]:
        import feedparser

        parsed = feedparser.parse(content)
        version = str(parsed.get("version") or "")
        feed_kind = "atom" if version.startswith("atom") else "rss"

        entries: List[FeedEntry] = []
        for item in parsed.entries:
            link = item.get("link")
            if not link:
                for link_obj in item.get("links", []) or []:
                    if link_obj.get("rel") in (None, "alternate") and link_obj.get("href"):
                        link = link_obj["href"]
                        break
            if not link:
                continue
            link = urljoin(base_url, link)

            inline_html = None
            contents = item.get("content")
            if isinstance(contents, list) and contents and contents[0].get("value"):
                inline_html = contents[0]["value"]

            entries.append(
                FeedEntry(
                    url=link,
                    title=item.get("title"),
                    lastmod=item.get("published") or item.get("updated"),
                    inline_html=inline_html,
                )
            )
        return entries, feed_kind

    # -- filtering ---------------------------------------------------------- #
    def _filter_entries(
        self,
        entries: List[FeedEntry],
        feed_url: str,
        cfg: Dict[str, Any],
        include: Optional[str],
        exclude: Optional[str],
    ) -> List[FeedEntry]:
        base_host = urlparse(feed_url).netloc
        seen: set = set()
        out: List[FeedEntry] = []
        for entry in entries:
            url = entry.url
            if not url.startswith(("http://", "https://")):
                continue
            if url in seen:
                continue
            if cfg["same_host_only"] and urlparse(url).netloc != base_host:
                continue
            if not _match_filters(url, include, exclude):
                continue
            seen.add(url)
            out.append(entry)
        return out

    async def _apply_robots(
        self, entries: List[FeedEntry], feed_url: str, client, cfg: Dict[str, Any]
    ) -> List[FeedEntry]:
        if not cfg["respect_robots"]:
            return entries
        try:
            parsed = urlparse(feed_url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            response = await client.get(robots_url, headers={"User-Agent": _USER_AGENT})
            if response.status_code >= 400:
                return entries
            rp = RobotFileParser()
            rp.parse(response.text.splitlines())
            return [e for e in entries if rp.can_fetch(_USER_AGENT, e.url)]
        except Exception as e:
            logger.debug("[WebFeedAccessor] robots.txt check skipped for %s: %s", feed_url, e)
            return entries

    # -- mirroring ---------------------------------------------------------- #
    async def _mirror(
        self, entries: List[FeedEntry], client, temp_dir: str, cfg: Dict[str, Any]
    ) -> Dict[str, Optional[str]]:
        """Fetch (or write inline) each page into temp_dir; return {url: lastmod}."""
        used: set = set()
        planned: List[Tuple[FeedEntry, str]] = []
        for entry in entries:
            relpath = _dedup_relpath(_entry_to_relpath(entry), entry.url, used)
            used.add(relpath)
            planned.append((entry, relpath))

        sem = asyncio.Semaphore(cfg["max_concurrency"])

        async def mirror_one(entry: FeedEntry, relpath: str) -> bool:
            async with sem:
                if cfg["politeness_delay"]:
                    await asyncio.sleep(cfg["politeness_delay"])
                dest = Path(temp_dir) / relpath
                await asyncio.to_thread(dest.parent.mkdir, parents=True, exist_ok=True)

                if entry.inline_html:
                    document = _wrap_inline_html(entry.title, entry.inline_html)
                    await asyncio.to_thread(dest.write_text, document, encoding="utf-8")
                    return True

                response = await client.get(entry.url, headers={"User-Agent": _USER_AGENT})
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type and not any(
                    token in content_type for token in ("html", "xml", "text")
                ):
                    logger.debug(
                        "[WebFeedAccessor] Skipping non-text page %s (%s)",
                        entry.url,
                        content_type,
                    )
                    return False
                await asyncio.to_thread(dest.write_bytes, response.content)
                return True

        results = await asyncio.gather(
            *(mirror_one(entry, relpath) for entry, relpath in planned),
            return_exceptions=True,
        )

        pages: Dict[str, Optional[str]] = {}
        for (entry, _relpath), result in zip(planned, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("[WebFeedAccessor] Failed to mirror %s: %s", entry.url, result)
                continue
            if result:
                pages[entry.url] = entry.lastmod
        return pages


# --------------------------------------------------------------------------- #
# Single-page detect-and-suggest (never auto-crawls)
# --------------------------------------------------------------------------- #
def _extract_attr(tag: str, name: str) -> Optional[str]:
    match = re.search(rf'{name}\s*=\s*"([^"]*)"', tag, re.I) or re.search(
        rf"{name}\s*=\s*'([^']*)'", tag, re.I
    )
    return match.group(1) if match else None


def _extract_feed_links(html: str, base_url: str) -> List[str]:
    """Find <link rel=alternate type=rss/atom> and <link rel=sitemap> hrefs."""
    out: List[str] = []
    for match in re.finditer(r"<link\b[^>]*>", html, re.I):
        tag = match.group(0)
        href = _extract_attr(tag, "href")
        if not href:
            continue
        rel = (_extract_attr(tag, "rel") or "").lower()
        typ = (_extract_attr(tag, "type") or "").lower()
        if "alternate" in rel and typ in ("application/rss+xml", "application/atom+xml"):
            out.append(urljoin(base_url, href))
        elif rel == "sitemap":
            out.append(urljoin(base_url, href))
    return out


def _classify_and_count(content: bytes) -> Optional[Tuple[str, int]]:
    """Lightly classify a candidate document and count its entries (no recursion)."""
    from defusedxml.ElementTree import fromstring

    try:
        root = fromstring(content)
    except Exception:
        try:
            import feedparser

            parsed = feedparser.parse(content)
            if parsed.entries:
                version = str(parsed.get("version") or "")
                kind = "atom" if version.startswith("atom") else "rss"
                return kind, len(parsed.entries)
        except Exception:
            pass
        return None

    name = _localname(root.tag)
    if name == "urlset":
        return "sitemap", sum(1 for el in root if _localname(el.tag) == "url")
    if name == "sitemapindex":
        return "sitemapindex", sum(1 for el in root if _localname(el.tag) == "sitemap")
    if name in ("rss", "feed"):
        try:
            import feedparser

            parsed = feedparser.parse(content)
            kind = "atom" if name == "feed" else "rss"
            return kind, len(parsed.entries)
        except Exception:
            return None
    return None


def _format_hint(feed_url: str, kind: str, count: int) -> str:
    if kind == "sitemap":
        what = f"a sitemap with {count} page(s)"
    elif kind == "sitemapindex":
        what = f"a sitemap index ({count} sub-sitemap(s))"
    elif kind == "atom":
        what = f"an Atom feed with {count} entr{'y' if count == 1 else 'ies'}"
    else:
        what = f"an RSS feed with {count} item(s)"
    return (
        f"Tip: this site exposes {what}. To ingest the whole site as a single "
        f"watchable resource, run add_resource('{feed_url}')."
    )


async def _gather_feed_candidates(client, page_url: str, page_content: bytes) -> List[str]:
    """Collect ordered candidate sitemap/feed URLs for a site.

    Sources, in priority order: robots.txt ``Sitemap:`` directives, ``<link
    rel=alternate|sitemap>`` autodiscovery in the page HTML, and conventional paths.
    Shared by the single-page hint probe and the whole-site auto-discovery fallback.
    """
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates: List[str] = []
    # 1. robots.txt Sitemap: directives
    try:
        response = await client.get(origin + "/robots.txt", headers={"User-Agent": _USER_AGENT})
        if response.status_code < 400:
            rp = RobotFileParser()
            rp.parse(response.text.splitlines())
            candidates.extend(rp.site_maps() or [])
    except Exception:
        pass
    # 2. <link rel=alternate/sitemap> autodiscovery from the page HTML
    try:
        if page_content:
            candidates.extend(
                _extract_feed_links(page_content.decode("utf-8", "replace"), page_url)
            )
    except Exception:
        pass
    # 3. conventional locations
    candidates.extend(origin + path for path in _COMMON_FEED_PATHS)

    seen: set = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]


async def _discover_feed_hint(url: str, request_validator) -> Optional[str]:
    url = str(url)
    if not url.startswith(("http://", "https://")):
        return None
    try:
        from openviking.utils.code_hosting_utils import is_git_repo_url

        if is_git_repo_url(url):
            return None
    except Exception:
        pass
    if looks_like_feed_url(url):
        return None  # already a feed — nothing to suggest
    ext = Path(urlparse(url).path).suffix.lower()
    if ext and ext not in _WEB_PAGE_EXTENSIONS:
        return None  # a downloadable file (pdf/img/...), not a webpage

    httpx = lazy_import("httpx")
    client_kwargs = _build_httpx_client_kwargs(request_validator, 5.0)
    async with httpx.AsyncClient(**client_kwargs) as client:
        # Fetch the page once for <link> autodiscovery (best-effort).
        page_content = b""
        try:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
            if (
                response.status_code < 400
                and "html" in response.headers.get("content-type", "").lower()
            ):
                page_content = response.content
        except Exception:
            pass

        for candidate in await _gather_feed_candidates(client, url, page_content):
            try:
                response = await client.get(candidate, headers={"User-Agent": _USER_AGENT})
                if response.status_code >= 400:
                    continue
                info = _classify_and_count(response.content)
                if info and info[1] > 0:
                    return _format_hint(candidate, info[0], info[1])
            except Exception:
                continue
    return None


async def discover_feed_hint(
    url: Union[str, Path],
    *,
    timeout: float = 2.5,
    request_validator=None,
) -> Optional[str]:
    """Best-effort probe: suggest whole-site ingestion when a single page is added.

    Hard-bounded by ``timeout`` and fully exception-swallowing — it must never
    block, slow, or fail the single-page add it is attached to. Returns a
    one-line hint string, or None when nothing relevant is found.
    """
    try:
        return await asyncio.wait_for(_discover_feed_hint(str(url), request_validator), timeout)
    except Exception:
        return None
