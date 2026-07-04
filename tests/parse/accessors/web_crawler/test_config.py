import pytest

from openviking.parse.accessors.web_crawler.config import CrawlConfig


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

    def test_playwright_timeout_non_positive_rejected(self):
        with pytest.raises(ValueError):
            CrawlConfig(playwright_timeout=0)
