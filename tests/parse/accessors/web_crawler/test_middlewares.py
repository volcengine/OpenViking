from unittest.mock import MagicMock

import pytest
from scrapy.exceptions import IgnoreRequest

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.middlewares import RequestValidatorMiddleware
from openviking.parse.accessors.web_crawler.resolver import (
    _VERIFIED_ADDRESSES,
    ValidatedAddressResolver,
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
    def setup_method(self):
        _VERIFIED_ADDRESSES.clear()

    def teardown_method(self):
        _VERIFIED_ADDRESSES.clear()

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

    def test_pins_address_returned_by_validator(self):
        mw = RequestValidatorMiddleware()
        spider = _make_spider(validator=lambda _url: "203.0.113.10")
        request = _make_request("https://EXAMPLE.com./redirected-target")

        mw.process_request(request, spider)

        assert _VERIFIED_ADDRESSES == {"example.com": "203.0.113.10"}

    def test_does_not_pin_when_private_networks_are_allowed(self):
        mw = RequestValidatorMiddleware()
        spider = _make_spider(validator=lambda _url: None)
        request = _make_request("https://internal.example/")

        mw.process_request(request, spider)

        assert _VERIFIED_ADDRESSES == {}


class TestValidatedAddressResolver:
    def setup_method(self):
        _VERIFIED_ADDRESSES.clear()

    def teardown_method(self):
        _VERIFIED_ADDRESSES.clear()

    def test_returns_pinned_address_without_resolving_again(self):
        _VERIFIED_ADDRESSES["example.com"] = "203.0.113.10"
        resolver = object.__new__(ValidatedAddressResolver)

        result = resolver.getHostByName("EXAMPLE.COM.")

        assert result.called
        assert result.result == "203.0.113.10"
