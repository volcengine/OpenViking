from unittest.mock import MagicMock

import pytest
from scrapy.exceptions import IgnoreRequest

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.middlewares import (
    ParserNetworkProxyMiddleware,
    RequestValidatorMiddleware,
)


def _make_spider(validator):
    spider = MagicMock()
    spider.config = CrawlConfig(request_validator=validator)
    return spider


def _make_request(url):
    request = MagicMock()
    request.url = url
    return request


class TestRequestValidatorMiddleware:
    def test_passes_through_when_validator_accepts(self):
        mw = RequestValidatorMiddleware()
        spider = _make_spider(validator=lambda _url: None)
        request = _make_request("https://example.com/")
        assert mw.process_request(request, spider) is None

    def test_raises_ignore_when_validator_rejects(self):
        mw = RequestValidatorMiddleware()

        def reject(url):
            raise ValueError(f"private address: {url}")

        spider = _make_spider(validator=reject)
        request = _make_request("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(IgnoreRequest) as exc_info:
            mw.process_request(request, spider)
        assert "private address" in str(exc_info.value)

    def test_no_op_when_validator_unset(self):
        mw = RequestValidatorMiddleware()
        spider = _make_spider(validator=None)
        request = _make_request("https://example.com/")
        assert mw.process_request(request, spider) is None

    def test_validator_receives_request_url(self):
        mw = RequestValidatorMiddleware()
        seen: list[str] = []
        spider = _make_spider(validator=lambda url: seen.append(url))
        request = _make_request("https://example.com/redirected-target")
        mw.process_request(request, spider)
        assert seen == ["https://example.com/redirected-target"]


class TestParserNetworkProxyMiddleware:
    @staticmethod
    def _request(url):
        request = MagicMock()
        request.url = url
        request.meta = {}
        return request

    @staticmethod
    def _spider(environment):
        spider = MagicMock()
        spider.network_env = environment
        return spider

    def test_sets_scheme_proxy_for_external_request(self):
        request = self._request("https://example.com/docs")
        spider = self._spider(
            {
                "HTTPS_PROXY": "http://proxy.example:8118",
                "NO_PROXY": "localhost,.byted.org,10.0.0.0/8",
            }
        )

        ParserNetworkProxyMiddleware().process_request(request, spider)

        assert request.meta["proxy"] == "http://proxy.example:8118"

    @pytest.mark.parametrize(
        "url",
        [
            "https://service.byted.org/docs",
            "http://10.2.3.4/internal",
        ],
    )
    def test_honors_domain_and_cidr_no_proxy(self, url):
        request = self._request(url)
        spider = self._spider(
            {
                "HTTP_PROXY": "http://proxy.example:8118",
                "HTTPS_PROXY": "http://proxy.example:8118",
                "NO_PROXY": ".byted.org,10.0.0.0/8",
            }
        )

        ParserNetworkProxyMiddleware().process_request(request, spider)

        assert request.meta["proxy"] is None

    def test_switches_proxy_when_redirect_changes_scheme(self):
        middleware = ParserNetworkProxyMiddleware()
        spider = self._spider(
            {
                "HTTP_PROXY": "http://http-proxy.example:8118",
                "HTTPS_PROXY": "http://https-proxy.example:8118",
            }
        )
        request = self._request("http://example.com/start")

        middleware.process_request(request, spider)
        assert request.meta["proxy"] == "http://http-proxy.example:8118"

        # Redirect requests inherit meta from the original request.
        request.url = "https://example.com/final"
        middleware.process_request(request, spider)
        assert request.meta["proxy"] == "http://https-proxy.example:8118"

    def test_no_proxy_marker_survives_native_proxy_middleware(self):
        middleware = ParserNetworkProxyMiddleware()
        spider = self._spider(
            {
                "HTTP_PROXY": "http://proxy.example:8118",
                "NO_PROXY": "10.0.0.0/8",
            }
        )
        request = self._request("http://10.2.3.4/internal")

        middleware.process_request(request, spider)

        assert request.meta["proxy"] is None
        assert request.meta["_openviking_parser_proxy_managed"] is True

    def test_preserves_explicit_request_proxy(self):
        request = self._request("https://example.com/docs")
        request.meta["proxy"] = "http://explicit.example:8080"
        spider = self._spider({"HTTPS_PROXY": "http://adapter.example:8118"})

        ParserNetworkProxyMiddleware().process_request(request, spider)

        assert request.meta["proxy"] == "http://explicit.example:8080"

    def test_leaves_native_proxy_selection_untouched_without_adapter_proxy(self):
        request = self._request("https://example.com/docs")
        spider = self._spider({"NO_PROXY": "localhost"})

        ParserNetworkProxyMiddleware().process_request(request, spider)

        assert request.meta == {}
