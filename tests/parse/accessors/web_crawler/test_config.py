import pytest

from openviking.parse.accessors.web_crawler.config import CrawlConfig
from openviking.parse.accessors.web_crawler.web_crawler import _build_settings


class TestCrawlConfigValidation:
    def test_defaults_are_valid(self):
        config = CrawlConfig()
        assert config.skip_download_links is True

    def test_depth_minus_one_unlimited_ok(self):
        CrawlConfig(depth=-1)

    def test_depth_minus_two_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(depth=-2)

    def test_max_pages_zero_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(max_pages=0)

    def test_max_pages_minus_one_unlimited_ok(self):
        CrawlConfig(max_pages=-1)

    def test_concurrency_zero_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(concurrency=0)

    def test_timeout_non_positive_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(timeout=0)
        with pytest.raises(ValueError):
            CrawlConfig(timeout=-1.0)

    def test_download_delay_negative_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(download_delay=-0.1)

    def test_retry_times_negative_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(retry_times=-1)

    def test_max_links_per_page_zero_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(max_links_per_page=0)

    def test_max_html_bytes_zero_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(max_html_bytes=0)


def test_unguarded_crawler_preserves_default_proxy_support():
    settings = _build_settings(CrawlConfig())

    assert settings.getbool("HTTPPROXY_ENABLED") is True
    assert (
        settings["DNS_RESOLVER"]
        != "openviking.parse.accessors.web_crawler.resolver.ValidatedAddressResolver"
    )


def test_guarded_crawler_uses_validated_address_resolver_without_proxy():
    settings = _build_settings(CrawlConfig(request_validator=lambda _url: ("8.8.8.8",)))

    assert (
        settings["DNS_RESOLVER"]
        == "openviking.parse.accessors.web_crawler.resolver.ValidatedAddressResolver"
    )
    assert settings.getbool("HTTPPROXY_ENABLED") is False
