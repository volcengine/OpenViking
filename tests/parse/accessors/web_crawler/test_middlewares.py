from unittest.mock import MagicMock

import pytest
from scrapy.exceptions import IgnoreRequest

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.middlewares import RequestValidatorMiddleware


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
