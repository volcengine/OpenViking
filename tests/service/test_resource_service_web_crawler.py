# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ResourceService web crawler integration."""

from unittest.mock import MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.resource_service import ResourceService
from openviking.utils.web_crawler import CrawledPage, CrawlResult
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


class MockResourceProcessor:
    async def process_resource(self, **kwargs):
        return {"root_uri": kwargs.get("to") or "viking://resources/test"}


class MockSkillProcessor:
    async def process_skill(self, **kwargs):
        return {"status": "ok"}


@pytest.fixture
def request_context() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("test_account", "test_user", "test_agent"),
        role=Role.USER,
    )


@pytest.fixture
def resource_service() -> ResourceService:
    return ResourceService(
        vikingdb=MagicMock(),
        viking_fs=MagicMock(),
        resource_processor=MockResourceProcessor(),
        skill_processor=MockSkillProcessor(),
        watch_scheduler=None,
    )


@pytest.mark.asyncio
async def test_add_resource_rejects_top_level_crawler_args(
    resource_service: ResourceService,
    request_context: RequestContext,
):
    with pytest.raises(
        InvalidArgumentError,
        match="Crawler options must be passed via args",
    ):
        await resource_service.add_resource(
            path="https://example.com/docs",
            ctx=request_context,
            depth=1,
        )


@pytest.mark.asyncio
async def test_add_resource_rejects_core_fields_inside_args(
    resource_service: ResourceService,
    request_context: RequestContext,
):
    with pytest.raises(
        InvalidArgumentError,
        match="args cannot include core add_resource fields",
    ):
        await resource_service.add_resource(
            path="https://example.com/docs",
            ctx=request_context,
            args={"path": "https://evil.example"},
        )


@pytest.mark.asyncio
async def test_crawl_and_add_resources_counts_child_status_error(
    resource_service: ResourceService,
    request_context: RequestContext,
    monkeypatch,
):
    class FakeWebCrawler:
        def __init__(self, config):
            self.config = config
            self.closed = False

        async def crawl(self, root_url):
            assert root_url == "https://example.com/docs"
            return CrawlResult(
                pages=[
                    CrawledPage(
                        url="https://example.com/docs",
                        depth=0,
                        title="Root",
                        content="<h1>Root</h1>",
                        content_type="text/html",
                    ),
                    CrawledPage(
                        url="https://example.com/docs/child",
                        depth=1,
                        title="Child",
                        content="<h1>Child</h1>",
                        content_type="text/html",
                    ),
                ],
                total_crawled=2,
            )

        async def close(self):
            self.closed = True

    monkeypatch.setattr("openviking.utils.web_crawler.WebCrawler", FakeWebCrawler)

    calls = []

    async def fake_add_resource(**kwargs):
        calls.append(kwargs)
        if kwargs.get("original_source") == "https://example.com/docs/child":
            return {"status": "error", "error": "parser failed"}
        return {"status": "success", "root_uri": kwargs.get("to")}

    resource_service.add_resource = fake_add_resource

    result = await resource_service._crawl_and_add_resources(
        root_url="https://example.com/docs",
        depth=1,
        max_pages=5,
        include_paths=None,
        exclude_paths=None,
        allow_external_links=False,
        ctx=request_context,
        parent_uri="viking://resources/example.com",
        root_uri="viking://resources/example.com/docs",
        instruction="",
        reason="",
        build_index=True,
        summarize=False,
    )

    assert len(calls) == 2
    assert all(call["args"] == {"depth": 0} for call in calls)
    assert result.root_updated is True
    assert result.child_added == 0
    assert result.child_failed == 1
