import pytest
from unittest.mock import MagicMock

from scrapy.exceptions import CloseSpider

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.scrapy_spider import OpenVikingWebSpider


def _make_spider(config=None, root_url="http://example.com/"):
    return OpenVikingWebSpider(
        root_url=root_url,
        config=config or CrawlConfig(),
        collector=[],
        download_collector=[],
    )


def _make_response(url="http://example.com/", body=b"", headers=None, depth=0):
    resp = MagicMock()
    resp.url = url
    resp.body = body
    resp.text = body.decode("utf-8", errors="ignore")
    headers_dict = headers or {"content-type": b"text/html"}
    resp.headers.get = lambda k, default=b"": headers_dict.get(k, default)
    resp.meta = {"depth": depth}
    resp.follow = MagicMock(return_value=MagicMock())
    return resp


async def _drive_parse(spider, response):
    items = []
    async for item in spider.parse(response):
        items.append(item)
    return items


class TestAcceptChildUrl:
    def test_accepts_same_host(self):
        spider = _make_spider()
        assert spider._accept_child_url("http://example.com/foo") is True

    def test_rejects_external_when_disallowed(self):
        spider = _make_spider()
        assert spider._accept_child_url("http://other.com/x") is False

    def test_accepts_external_when_allowed(self):
        spider = _make_spider(CrawlConfig(allow_external_links=True, max_pages=10))
        assert spider._accept_child_url("http://other.com/x") is True

    def test_rejects_non_http_scheme(self):
        spider = _make_spider()
        assert spider._accept_child_url("ftp://example.com/x") is False
        assert spider._accept_child_url("file:///etc/passwd") is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/a.css",
            "http://example.com/a.js",
            "http://example.com/a.png",
            "http://example.com/a.pdf",
            "http://example.com/a.zip",
            "http://example.com/a.txt",
        ],
    )
    def test_rejects_non_page_extensions(self, url):
        assert _make_spider()._accept_child_url(url) is False

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.com/a.pdf",
            "http://example.com/a.zip",
            "http://example.com/a.txt",
            "http://example.com/a.md",
            "http://example.com/a.docx",
        ],
    )
    def test_accepts_download_extensions_as_downloads(self, url):
        assert _make_spider()._accept_download_url(url) is True

    def test_rejects_download_when_skip_download_links_enabled(self):
        spider = _make_spider(CrawlConfig(skip_download_links=True))
        html = '<a href="/llms.txt">txt</a>'
        assert spider._extract_download_urls(html, "http://example.com/", 1) == []

    def test_rejects_external_download_when_disallowed(self):
        spider = _make_spider()
        assert spider._accept_download_url("http://other.com/a.pdf") is False

    def test_accepts_external_download_when_allowed(self):
        spider = _make_spider(CrawlConfig(allow_external_links=True))
        assert spider._accept_download_url("http://other.com/a.pdf") is True

    def test_rejects_when_include_paths_miss(self):
        spider = _make_spider(CrawlConfig(include_paths=["/docs/"]))
        assert spider._accept_child_url("http://example.com/blog/x") is False

    def test_accepts_when_include_paths_hit(self):
        spider = _make_spider(CrawlConfig(include_paths=["/docs/"]))
        assert spider._accept_child_url("http://example.com/docs/intro") is True

    def test_rejects_when_exclude_paths_hit(self):
        spider = _make_spider(CrawlConfig(exclude_paths=["/admin/"]))
        assert spider._accept_child_url("http://example.com/admin/x") is False

    def test_include_paths_is_prefix_not_substring(self):
        spider = _make_spider(CrawlConfig(include_paths=["/docs/"]))
        assert spider._accept_child_url("http://example.com/blog/docs-tips") is False
        assert spider._accept_child_url("http://example.com/api/v1/getdocs") is False

    def test_exclude_paths_is_prefix_not_substring(self):
        spider = _make_spider(CrawlConfig(exclude_paths=["/admin/"]))
        assert spider._accept_child_url("http://example.com/blog/admin-tips") is True

    def test_rejects_when_validator_raises(self):
        def blocker(_):
            raise ValueError("blocked")
        spider = _make_spider(CrawlConfig(request_validator=blocker))
        assert spider._accept_child_url("http://example.com/anywhere") is False


class TestExtractChildUrls:
    def test_strips_anchor_and_dedup(self):
        spider = _make_spider()
        html = '<a href="/a#x">x</a><a href="/a">y</a>'
        assert spider._extract_child_urls(html, "http://example.com/") == [
            "http://example.com/a"
        ]

    def test_skips_non_http_schemes(self):
        spider = _make_spider()
        html = (
            '<a href="javascript:void(0)">x</a>'
            '<a href="mailto:a@b.com">y</a>'
            '<a href="tel:123">z</a>'
            '<a href="#frag">w</a>'
        )
        assert spider._extract_child_urls(html, "http://example.com/") == []

    def test_resolves_relative_with_urljoin(self):
        spider = _make_spider()
        html = '<a href="docs/intro">x</a>'
        assert spider._extract_child_urls(html, "http://example.com/") == [
            "http://example.com/docs/intro"
        ]

    def test_caps_at_max_links_per_page(self):
        spider = _make_spider(CrawlConfig(max_links_per_page=2))
        html = "".join(f'<a href="/p{i}">x</a>' for i in range(5))
        assert len(spider._extract_child_urls(html, "http://example.com/")) == 2

    def test_returns_empty_on_blank_html(self):
        spider = _make_spider()
        assert spider._extract_child_urls("", "http://example.com/") == []


class TestSuccessAtLimit:
    def test_unlimited_never_at_limit(self):
        spider = _make_spider(CrawlConfig(max_pages=-1))
        spider._success_count = 100
        assert spider._success_at_limit() is False

    def test_at_limit_when_count_equals_max(self):
        spider = _make_spider(CrawlConfig(max_pages=10))
        spider._success_count = 10
        assert spider._success_at_limit() is True

    def test_below_limit(self):
        spider = _make_spider(CrawlConfig(max_pages=10))
        spider._success_count = 9
        assert spider._success_at_limit() is False


_RICH_HTML = (
    "<html><head><title>Example</title></head><body><article>"
    + "<p>" + ("This is a meaningful paragraph for the crawler. " * 20) + "</p>"
    + "<p>" + ("Another paragraph with enough characters to extract. " * 20) + "</p>"
    + "</article></body></html>"
).encode("utf-8")


class TestParseGate:
    async def test_early_return_when_limit_reached(self):
        spider = _make_spider(CrawlConfig(max_pages=10, fallback_playwright=False))
        spider._success_count = 10
        resp = _make_response(body=_RICH_HTML)
        items = await _drive_parse(spider, resp)
        assert items == []
        assert spider._success_count == 10
        assert spider.collector == []

    async def test_appends_success_and_increments_count(self):
        spider = _make_spider(CrawlConfig(max_pages=10, fallback_playwright=False))
        resp = _make_response(body=_RICH_HTML)
        await _drive_parse(spider, resp)
        assert spider._success_count == 1
        success = [p for p in spider.collector if p.status == "success"]
        assert len(success) == 1
        assert success[0].html == _RICH_HTML.decode("utf-8")
        assert success[0].source == "scrapy_static"

    async def test_download_links_count_toward_max_pages(self):
        spider = _make_spider(
            CrawlConfig(
                depth=1,
                max_pages=2,
                skip_download_links=False,
                fallback_playwright=False,
            )
        )
        body = (
            b"<html><body><h1>Home</h1>"
            b'<a href="/llms.txt">txt</a>'
            b'<a href="/guide">guide</a>'
            b"</body></html>"
        )
        resp = _make_response(body=body)
        items = await _drive_parse(spider, resp)
        assert spider._success_count == 2
        assert [download.url for download in spider.download_collector] == [
            "http://example.com/llms.txt"
        ]
        assert items == []

    async def test_second_call_blocked_after_reaching_max(self):
        spider = _make_spider(CrawlConfig(max_pages=1, fallback_playwright=False))
        resp = _make_response(body=_RICH_HTML)
        await _drive_parse(spider, resp)
        assert spider._success_count == 1
        await _drive_parse(spider, resp)
        assert spider._success_count == 1
        success = [p for p in spider.collector if p.status == "success"]
        assert len(success) == 1

    async def test_reaching_limit_closes_scrapy_engine(self):
        spider = _make_spider(CrawlConfig(max_pages=1, fallback_playwright=False))
        spider.crawler = MagicMock()
        spider.crawler.engine = MagicMock()
        resp = _make_response(body=_RICH_HTML)

        with pytest.raises(CloseSpider) as exc_info:
            await _drive_parse(spider, resp)

        assert exc_info.value.reason == "max_pages_reached"
        assert spider._success_count == 1
        assert len(spider.collector) == 1

    async def test_skipped_on_non_html_response(self):
        spider = _make_spider(CrawlConfig(fallback_playwright=False))
        resp = _make_response(
            body=b"binary",
            headers={"content-type": b"application/octet-stream"},
        )
        await _drive_parse(spider, resp)
        assert spider._success_count == 0
        statuses = [p.status for p in spider.collector]
        assert statuses == ["skipped"]

    async def test_failed_on_validator_rejecting_entry(self):
        def blocker(_):
            raise ValueError("blocked by SSRF")
        spider = _make_spider(CrawlConfig(request_validator=blocker, fallback_playwright=False))
        resp = _make_response(body=_RICH_HTML)
        await _drive_parse(spider, resp)
        assert spider._success_count == 0
        statuses = [p.status for p in spider.collector]
        assert statuses == ["failed"]
