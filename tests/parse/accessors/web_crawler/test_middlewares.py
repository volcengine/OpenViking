from types import SimpleNamespace
from unittest.mock import patch

import pytest
from scrapy import Request, Spider
from scrapy.crawler import Crawler
from scrapy.downloadermiddlewares.httpproxy import HttpProxyMiddleware
from scrapy.downloadermiddlewares.redirect import RedirectMiddleware
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.settings import Settings

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.middlewares import (
    ParserNetworkProxyMiddleware,
    RequestValidatorMiddleware,
)


class TestParserRequestRouting:
    def test_raises_ignore_when_validator_rejects(self):
        def reject(url):
            raise ValueError(f"private address: {url}")

        spider = SimpleNamespace(config=CrawlConfig(request_validator=reject))
        request = Request("http://169.254.169.254/latest/meta-data/")
        with pytest.raises(IgnoreRequest, match="private address"):
            RequestValidatorMiddleware().process_request(request, spider)

    def test_redirects_revalidate_and_select_proxy_or_direct_route(self):
        environment = {
            "HTTP_PROXY": "http://http-proxy.example:8118",
            "HTTPS_PROXY": "http://https-proxy.example:8118",
            "NO_PROXY": "93.184.0.0/16",
        }
        seen = []
        spider = SimpleNamespace(
            config=CrawlConfig(request_validator=seen.append),
            network_env=environment,
        )
        validator = RequestValidatorMiddleware()
        routing = ParserNetworkProxyMiddleware()
        redirects = RedirectMiddleware.from_crawler(Crawler(Spider, Settings()))

        with patch.dict("os.environ", environment, clear=True):
            native_proxy = HttpProxyMiddleware()
            request = Request("http://example.com/start")
            validator.process_request(request, spider)
            routing.process_request(request, spider)
            native_proxy.process_request(request, spider)
            assert request.meta["proxy"] == environment["HTTP_PROXY"]

            response = Response(
                request.url, status=301, headers={"Location": "https://example.com/final"}
            )
            request = redirects.process_response(request, response, spider)
            validator.process_request(request, spider)
            routing.process_request(request, spider)
            native_proxy.process_request(request, spider)
            assert request.meta["proxy"] == environment["HTTPS_PROXY"]

            response = Response(
                request.url, status=302, headers={"Location": "http://93.184.216.34/article"}
            )
            request = redirects.process_response(request, response, spider)
            validator.process_request(request, spider)
            routing.process_request(request, spider)
            native_proxy.process_request(request, spider)
            assert request.meta["proxy"] is None

        assert seen == [
            "http://example.com/start",
            "https://example.com/final",
            "http://93.184.216.34/article",
        ]
