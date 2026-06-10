# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for crawl URL filtering rules."""

from openviking.utils.crawl_filter import CrawlConfig, CrawlFilter


def test_filter_applies_include_and_exclude_paths_with_exclude_priority():
    crawl_filter = CrawlFilter(
        CrawlConfig(
            include_paths=["/docs/*"],
            exclude_paths=["/docs/private/*"],
        )
    )

    result = crawl_filter.filter(
        [
            "https://example.com/docs/intro",
            "https://example.com/blog/intro",
            "https://example.com/docs/private/secret",
        ],
        base_url="https://example.com/docs",
    )

    assert result.accepted == ["https://example.com/docs/intro"]
    assert result.stats.rejected_by_path == 2
