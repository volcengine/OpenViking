# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Unit tests for WebFeedAccessor (whole-site ingestion via sitemap / RSS / Atom)."""

import pytest

from openviking.parse.accessors import (
    AccessorRegistry,
    GitAccessor,
    HTTPAccessor,
    WebFeedAccessor,
)
from openviking.parse.accessors.web_feed_accessor import (
    FeedEntry,
    _classify_and_count,
    _entry_to_relpath,
    _extract_feed_links,
    discover_feed_hint,
    looks_like_feed_url,
    url_to_relpath,
)

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


# --------------------------------------------------------------------------- #
# Fake httpx client (dependency-free; no real network)
# --------------------------------------------------------------------------- #
class _FakeStatusError(Exception):
    pass


class _FakeResponse:
    def __init__(self, status_code=200, content=b"", content_type="text/html"):
        self.status_code = status_code
        self.content = content if isinstance(content, bytes) else content.encode("utf-8")
        self.headers = {"content-type": content_type}

    @property
    def text(self):
        return self.content.decode("utf-8", "replace")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _FakeStatusError(f"status {self.status_code}")


def _make_fake_client(routes):
    """routes: dict url -> _FakeResponse (missing urls -> 404)."""

    class _FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, headers=None):
            resp = routes.get(url)
            if resp is None:
                return _FakeResponse(404, b"not found", "text/plain")
            return resp

    return _FakeClient


@pytest.fixture
def patch_httpx(monkeypatch):
    """Patch httpx.AsyncClient with a fake serving the given routes."""

    def _apply(routes):
        import httpx

        monkeypatch.setattr(httpx, "AsyncClient", _make_fake_client(routes))

    return _apply


def _urlset(urls_with_lastmod):
    items = "".join(
        f"<url><loc>{u}</loc>" + (f"<lastmod>{lm}</lastmod>" if lm else "") + "</url>"
        for u, lm in urls_with_lastmod
    )
    return f'<?xml version="1.0"?><urlset xmlns="{SITEMAP_NS}">{items}</urlset>'.encode()


def _sitemapindex(sub_urls):
    items = "".join(f"<sitemap><loc>{u}</loc></sitemap>" for u in sub_urls)
    return (
        f'<?xml version="1.0"?><sitemapindex xmlns="{SITEMAP_NS}">{items}</sitemapindex>'.encode()
    )


def _page(title):
    return f"<html><head><title>{title}</title></head><body><h1>{title}</h1><p>body</p></body></html>".encode()


# --------------------------------------------------------------------------- #
# can_handle
# --------------------------------------------------------------------------- #
class TestCanHandle:
    @pytest.fixture
    def accessor(self):
        return WebFeedAccessor()

    def test_priority(self, accessor):
        assert accessor.priority == 60

    @pytest.mark.parametrize(
        "url",
        [
            "https://blog.openviking.ai/sitemap.xml",
            "https://t0saki.com/sitemap-index.xml",
            "https://t0saki.com/sitemap_index.xml",
            "https://t0saki.com/sitemap-0.xml",
            "https://t0saki.com/rss.xml",
            "https://example.com/feed.xml",
            "https://example.com/atom.xml",
            "https://example.com/index.xml",
            "https://example.com/feed",
            "https://example.com/blog/feed/",
        ],
    )
    def test_handles_feed_like(self, accessor, url):
        assert accessor.can_handle(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/post/foo/",
            "https://example.com/data.xml",
            "https://example.com/page.html",
            "/tmp/sitemap.xml",
            "git@github.com:org/repo.git",
        ],
    )
    def test_rejects_non_feed(self, accessor, url):
        assert accessor.can_handle(url) is False

    def test_explicit_override(self, accessor):
        assert accessor.can_handle("https://example.com/post/foo/", site=True) is True
        assert accessor.can_handle("https://example.com/sitemap.xml", site=False) is False
        # alias keys
        assert accessor.can_handle("https://example.com/x", sitemap=True) is True
        assert accessor.can_handle("https://example.com/x", feed=True) is True


# --------------------------------------------------------------------------- #
# registry routing
# --------------------------------------------------------------------------- #
class TestRegistryRouting:
    @pytest.fixture
    def registry(self):
        return AccessorRegistry()

    def test_sitemap_routes_to_web_feed(self, registry):
        acc = registry.get_accessor("https://blog.openviking.ai/sitemap.xml")
        assert isinstance(acc, WebFeedAccessor)

    def test_git_url_still_wins(self, registry):
        acc = registry.get_accessor("https://github.com/volcengine/OpenViking")
        assert isinstance(acc, GitAccessor)

    def test_plain_page_routes_to_http(self, registry):
        acc = registry.get_accessor("https://example.com/article")
        assert isinstance(acc, HTTPAccessor)

    def test_override_routes_plain_page_to_web_feed(self, registry):
        acc = registry.get_accessor("https://example.com/article", site=True)
        assert isinstance(acc, WebFeedAccessor)


# --------------------------------------------------------------------------- #
# url -> relpath
# --------------------------------------------------------------------------- #
class TestRelpath:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://h/", "index.html"),
            ("https://h/a/b", "a/b.html"),
            ("https://h/post/foo/", "post/foo.html"),
            ("https://h/post/foo/index.html", "post/foo/index.html"),
            ("https://h/about.php", "about.html"),
        ],
    )
    def test_url_to_relpath(self, url, expected):
        assert url_to_relpath(url) == expected

    def test_entry_title_used_when_present(self):
        assert (
            _entry_to_relpath(FeedEntry(url="https://h/x", title="Hello World"))
            == "Hello World.html"
        )

    def test_entry_title_cjk(self):
        assert _entry_to_relpath(FeedEntry(url="https://h/x", title="台湾游记")) == "台湾游记.html"

    def test_entry_no_title_falls_back_to_url(self):
        assert _entry_to_relpath(FeedEntry(url="https://h/post/bar/")) == "post/bar.html"

    def test_title_with_slash_is_sanitized(self):
        rel = _entry_to_relpath(FeedEntry(url="https://h/x", title="a/b: c"))
        assert "/" not in rel[:-5]  # no path separator in the stem


# --------------------------------------------------------------------------- #
# classify-and-count (used by the single-page hint)
# --------------------------------------------------------------------------- #
class TestClassify:
    def test_urlset(self):
        content = _urlset([("https://h/a", None), ("https://h/b", "2026-01-01")])
        assert _classify_and_count(content) == ("sitemap", 2)

    def test_sitemapindex(self):
        assert _classify_and_count(_sitemapindex(["https://h/s0.xml"])) == ("sitemapindex", 1)

    def test_rss(self):
        rss = b'<?xml version="1.0"?><rss version="2.0"><channel><item><link>https://h/a</link></item></channel></rss>'
        assert _classify_and_count(rss) == ("rss", 1)

    def test_garbage_returns_none(self):
        assert _classify_and_count(b"not xml at all") is None

    def test_extract_feed_links(self):
        html = (
            '<link rel="alternate" type="application/rss+xml" href="/rss.xml">'
            '<link rel="alternate" type="application/atom+xml" href="https://h/atom.xml">'
            '<link rel="sitemap" href="/sitemap.xml">'
            '<link rel="stylesheet" href="/x.css">'
        )
        links = _extract_feed_links(html, "https://h/page")
        assert "https://h/rss.xml" in links
        assert "https://h/atom.xml" in links
        assert "https://h/sitemap.xml" in links
        assert all("x.css" not in link for link in links)


# --------------------------------------------------------------------------- #
# access() — sitemap mirroring
# --------------------------------------------------------------------------- #
class TestAccessSitemap:
    async def test_flat_urlset_mirrors_pages(self, patch_httpx):
        base = "https://blog.example.com"
        page_urls = [f"{base}/post/p{i}/" for i in range(10)]
        routes = {
            f"{base}/sitemap.xml": _FakeResponse(content=_urlset([(u, None) for u in page_urls]))
        }
        for i, u in enumerate(page_urls):
            routes[u] = _FakeResponse(content=_page(f"Post {i}"))
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/sitemap.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.is_temporary is True
            assert resource.path.is_dir()
            assert resource.meta["original_filename"] == "blog.example.com"
            assert resource.meta["page_count"] == 10
            assert resource.meta["url_type"] == "sitemap"
            html_files = list(resource.path.rglob("*.html"))
            assert len(html_files) == 10
            # mirrors URL path structure (post/p0.html ...)
            assert (resource.path / "post" / "p0.html").exists()
        finally:
            resource.cleanup()

    async def test_failed_page_is_skipped(self, patch_httpx):
        base = "https://blog.example.com"
        page_urls = [f"{base}/post/p{i}/" for i in range(5)]
        routes = {
            f"{base}/sitemap.xml": _FakeResponse(content=_urlset([(u, None) for u in page_urls]))
        }
        for i, u in enumerate(page_urls):
            routes[u] = _FakeResponse(content=_page(f"Post {i}"))
        routes[page_urls[2]] = _FakeResponse(500, b"err", "text/plain")  # one fails
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/sitemap.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["page_count"] == 4
        finally:
            resource.cleanup()

    async def test_sitemapindex_recursion(self, patch_httpx):
        base = "https://t0saki.com"
        page_urls = [f"{base}/posts/p{i}/" for i in range(3)]
        routes = {
            f"{base}/sitemap-index.xml": _FakeResponse(
                content=_sitemapindex([f"{base}/sitemap-0.xml"])
            ),
            f"{base}/sitemap-0.xml": _FakeResponse(content=_urlset([(u, None) for u in page_urls])),
        }
        for i, u in enumerate(page_urls):
            routes[u] = _FakeResponse(content=_page(f"P{i}"))
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(
            f"{base}/sitemap-index.xml", respect_robots=False, politeness_delay=0
        )
        try:
            assert resource.meta["page_count"] == 3
            assert resource.meta["url_type"] == "sitemap"
        finally:
            resource.cleanup()

    async def test_max_pages_truncates(self, patch_httpx):
        base = "https://blog.example.com"
        page_urls = [f"{base}/p{i}/" for i in range(10)]
        routes = {
            f"{base}/sitemap.xml": _FakeResponse(content=_urlset([(u, None) for u in page_urls]))
        }
        for u in page_urls:
            routes[u] = _FakeResponse(content=_page("x"))
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(
            f"{base}/sitemap.xml", respect_robots=False, politeness_delay=0, max_pages=3
        )
        try:
            assert resource.meta["page_count"] == 3
        finally:
            resource.cleanup()

    async def test_same_host_only_filters_external(self, patch_httpx):
        base = "https://blog.example.com"
        internal = f"{base}/keep/"
        external = "https://other.com/drop/"
        routes = {
            f"{base}/sitemap.xml": _FakeResponse(
                content=_urlset([(internal, None), (external, None)])
            ),
            internal: _FakeResponse(content=_page("keep")),
            external: _FakeResponse(content=_page("drop")),
        }
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/sitemap.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["page_count"] == 1
            assert internal in resource.meta["pages"]
            assert external not in resource.meta["pages"]
        finally:
            resource.cleanup()


# --------------------------------------------------------------------------- #
# access() — auto-discovery from a bare domain / HTML page (args={"site": True})
# --------------------------------------------------------------------------- #
class TestAutoDiscovery:
    async def test_bare_domain_discovers_sitemap_via_robots(self, patch_httpx):
        base = "https://t0saki.example"
        page_urls = [f"{base}/posts/p{i}/" for i in range(3)]
        routes = {
            base: _FakeResponse(content=b"<html><body>home</body></html>"),
            f"{base}/robots.txt": _FakeResponse(
                content=f"User-agent: *\nSitemap: {base}/sitemap.xml\n", content_type="text/plain"
            ),
            f"{base}/sitemap.xml": _FakeResponse(content=_urlset([(u, None) for u in page_urls])),
        }
        for i, u in enumerate(page_urls):
            routes[u] = _FakeResponse(content=_page(f"P{i}"))
        patch_httpx(routes)

        acc = WebFeedAccessor()
        # access() simulates the args={"site": True} routing (can_handle override).
        resource = await acc.access(base, respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["page_count"] == 3
            assert resource.meta["feed_url"] == f"{base}/sitemap.xml"
            assert resource.meta["original_filename"] == "t0saki.example"
        finally:
            resource.cleanup()

    async def test_html_page_discovers_feed_via_link_tag(self, patch_httpx):
        base = "https://blog.example"
        page_html = (
            "<html><head>"
            f'<link rel="alternate" type="application/rss+xml" href="{base}/rss.xml">'
            "</head><body>post</body></html>"
        ).encode()
        rss = (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            f"<item><title>One</title><link>{base}/a/</link></item>"
            "</channel></rss>"
        ).encode()
        routes = {
            f"{base}/some-article": _FakeResponse(content=page_html),
            f"{base}/rss.xml": _FakeResponse(content=rss, content_type="application/rss+xml"),
            f"{base}/a/": _FakeResponse(content=_page("A")),
        }
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(
            f"{base}/some-article", respect_robots=False, politeness_delay=0
        )
        try:
            assert resource.meta["url_type"] == "rss"
            assert resource.meta["feed_url"] == f"{base}/rss.xml"
        finally:
            resource.cleanup()

    async def test_no_feed_discoverable_raises(self, patch_httpx):
        base = "https://nofeed.example"
        routes = {base: _FakeResponse(content=b"<html><body>nothing</body></html>")}
        patch_httpx(routes)
        acc = WebFeedAccessor()
        with pytest.raises(RuntimeError, match="auto-discovered"):
            await acc.access(base, respect_robots=False, politeness_delay=0)


# --------------------------------------------------------------------------- #
# access() — RSS / Atom
# --------------------------------------------------------------------------- #
class TestAccessFeed:
    async def test_rss_summary_only_fetches_links(self, patch_httpx):
        base = "https://t0saki.com"
        items = [(f"{base}/posts/a/", "Post A"), (f"{base}/posts/b/", "Post B")]
        rss = (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            + "".join(
                f"<item><title>{t}</title><link>{u}</link><description>summary</description></item>"
                for u, t in items
            )
            + "</channel></rss>"
        ).encode()
        routes = {f"{base}/rss.xml": _FakeResponse(content=rss, content_type="application/rss+xml")}
        for u, _ in items:
            routes[u] = _FakeResponse(content=_page("full"))
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/rss.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["url_type"] == "rss"
            assert resource.meta["page_count"] == 2
            # titles used as flat filenames
            assert (resource.path / "Post A.html").exists()
            assert (resource.path / "Post B.html").exists()
        finally:
            resource.cleanup()

    async def test_rss_inline_content_is_used_without_fetch(self, patch_httpx):
        base = "https://t0saki.com"
        rss = (
            '<?xml version="1.0"?>'
            '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
            "<channel><item><title>Inline</title><link>https://t0saki.com/posts/inline/</link>"
            "<content:encoded><![CDATA[<p>FULL INLINE BODY</p>]]></content:encoded>"
            "</item></channel></rss>"
        ).encode()
        # Note: deliberately DO NOT register the article URL — if access tried to
        # fetch it we'd get a 404 and page_count would drop to 0.
        routes = {f"{base}/rss.xml": _FakeResponse(content=rss, content_type="application/rss+xml")}
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/rss.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["page_count"] == 1
            saved = (resource.path / "Inline.html").read_text(encoding="utf-8")
            assert "FULL INLINE BODY" in saved
        finally:
            resource.cleanup()

    async def test_atom_feed(self, patch_httpx):
        base = "https://example.org"
        atom = (
            '<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            '<entry><title>Entry One</title><link href="https://example.org/e1/"/></entry>'
            "</feed>"
        ).encode()
        routes = {
            f"{base}/atom.xml": _FakeResponse(content=atom, content_type="application/atom+xml"),
            f"{base}/e1/": _FakeResponse(content=_page("E1")),
        }
        patch_httpx(routes)

        acc = WebFeedAccessor()
        resource = await acc.access(f"{base}/atom.xml", respect_robots=False, politeness_delay=0)
        try:
            assert resource.meta["url_type"] == "atom"
            assert resource.meta["page_count"] == 1
        finally:
            resource.cleanup()


# --------------------------------------------------------------------------- #
# discover_feed_hint (single-page detect-and-suggest)
# --------------------------------------------------------------------------- #
class TestDiscoverHint:
    async def test_hint_from_robots_sitemap(self, patch_httpx):
        base = "https://blog.example.com"
        routes = {
            f"{base}/robots.txt": _FakeResponse(
                content=f"User-agent: *\nSitemap: {base}/sitemap.xml\n", content_type="text/plain"
            ),
            f"{base}/sitemap.xml": _FakeResponse(
                content=_urlset([(f"{base}/p{i}/", None) for i in range(3)])
            ),
            f"{base}/article": _FakeResponse(content=_page("article")),
        }
        patch_httpx(routes)

        hint = await discover_feed_hint(f"{base}/article", timeout=5)
        assert hint is not None
        assert "3 page(s)" in hint
        assert f"{base}/sitemap.xml" in hint

    async def test_hint_from_html_autodiscovery(self, patch_httpx):
        base = "https://blog.example.com"
        page_html = (
            '<html><head><link rel="alternate" type="application/rss+xml" '
            f'href="{base}/rss.xml"></head><body>hi</body></html>'
        ).encode()
        rss = (
            '<?xml version="1.0"?><rss version="2.0"><channel>'
            "<item><link>https://blog.example.com/a/</link></item>"
            "<item><link>https://blog.example.com/b/</link></item>"
            "</channel></rss>"
        ).encode()
        routes = {
            f"{base}/article": _FakeResponse(content=page_html),
            f"{base}/rss.xml": _FakeResponse(content=rss, content_type="application/rss+xml"),
            # robots.txt missing -> 404, fine
        }
        patch_httpx(routes)

        hint = await discover_feed_hint(f"{base}/article", timeout=5)
        assert hint is not None
        assert "RSS feed with 2 item(s)" in hint

    async def test_no_hint_when_nothing_found(self, patch_httpx):
        base = "https://nofeed.example.com"
        routes = {f"{base}/article": _FakeResponse(content=_page("article"))}
        patch_httpx(routes)
        assert await discover_feed_hint(f"{base}/article", timeout=5) is None

    async def test_no_hint_for_feed_url_itself(self, patch_httpx):
        patch_httpx({})
        assert await discover_feed_hint("https://x.com/sitemap.xml", timeout=5) is None

    async def test_no_hint_for_non_html_extension(self, patch_httpx):
        patch_httpx({})
        assert await discover_feed_hint("https://x.com/file.pdf", timeout=5) is None


# --------------------------------------------------------------------------- #
# looks_like_feed_url (standalone)
# --------------------------------------------------------------------------- #
def test_looks_like_feed_url():
    assert looks_like_feed_url("https://h/sitemap.xml")
    assert looks_like_feed_url("https://h/rss.xml")
    assert not looks_like_feed_url("https://h/post")
    assert not looks_like_feed_url("/tmp/sitemap.xml")
