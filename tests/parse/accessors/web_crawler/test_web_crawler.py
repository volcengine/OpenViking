# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import os
from unittest.mock import AsyncMock, patch

import pytest

from openviking.parse.accessors.web_crawler import web_crawler
from openviking.parse.network import (
    ParserNetworkContext,
    ParserNetworkOperation,
    register_parser_network_adapter,
    reset_parser_network_adapter,
)


class _CrawlerNetworkAdapter:
    def __init__(self) -> None:
        self.contexts: list[ParserNetworkContext] = []

    def supports(self, context: ParserNetworkContext) -> bool:
        self.contexts.append(context)
        return context.operation is ParserNetworkOperation.WEB_CRAWL

    def create_httpx_transport(self, context: ParserNetworkContext):
        return None

    def create_urllib_opener(self, context: ParserNetworkContext):
        return None

    def subprocess_env_overlay(self, context: ParserNetworkContext):
        return {"HTTPS_PROXY": "http://proxy.example:8118"}


@pytest.fixture(autouse=True)
def _reset_adapter():
    reset_parser_network_adapter()
    yield
    reset_parser_network_adapter()


async def test_crawl_passes_adapter_overlay_to_spawned_worker() -> None:
    adapter = _CrawlerNetworkAdapter()
    register_parser_network_adapter(adapter)
    captured: dict[str, object] = {}

    class ResultQueue:
        def close(self):
            pass

        def join_thread(self):
            pass

    class Process:
        exitcode = 0

        def __init__(self, *, target, args):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            captured["started"] = True

        def is_alive(self):
            return False

    class Context:
        def Queue(self):
            return ResultQueue()

        def Process(self, *, target, args):
            return Process(target=target, args=args)

    crawler = web_crawler.ScrapyWebCrawler(web_crawler.CrawlConfig())
    with (
        patch.object(web_crawler.multiprocessing, "get_context", return_value=Context()),
        patch.object(
            crawler,
            "_wait_worker_result",
            new=AsyncMock(return_value=([], [], None)),
        ),
    ):
        await crawler.crawl("https://example.com")

    assert captured["started"] is True
    assert captured["target"] is web_crawler._run_crawl_worker
    assert captured["args"][3] == {"HTTPS_PROXY": "http://proxy.example:8118"}
    assert any(
        context.operation is ParserNetworkOperation.WEB_CRAWL
        and context.source_url == "https://example.com"
        for context in adapter.contexts
    )


def test_crawl_overlay_exists_only_inside_worker() -> None:
    seen: dict[str, str | None] = {}

    def worker(_root_url, _config, _result_queue, _network_env):
        seen["https_proxy"] = os.environ.get("HTTPS_PROXY")

    with (
        patch.dict(os.environ, {}, clear=True),
        patch.object(web_crawler, "_run_crawl_worker_with_environment", worker),
    ):
        web_crawler._run_crawl_worker(
            "https://example.com",
            web_crawler.CrawlConfig(),
            object(),
            {"HTTPS_PROXY": "http://proxy.example:8118"},
        )
        assert "HTTPS_PROXY" not in os.environ

    assert seen["https_proxy"] == "http://proxy.example:8118"


def test_settings_validate_before_applying_scrapy_proxy() -> None:
    settings = web_crawler._build_settings(web_crawler.CrawlConfig())
    middlewares = settings.getdict("DOWNLOADER_MIDDLEWARES")

    assert (
        middlewares["openviking.parse.accessors.web_crawler.middlewares.RequestValidatorMiddleware"]
        == 50
    )
    assert middlewares["scrapy.downloadermiddlewares.httpproxy.HttpProxyMiddleware"] == 750
