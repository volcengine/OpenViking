# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json

import httpx

from openviking.session.train.components.remote import (
    DEFAULT_REMOTE_CASE_PAGE_SIZE,
    RemoteCaseLoader,
)


def test_remote_case_loader_default_page_size_is_200(monkeypatch):
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"cases": [], "next_cursor": None})

    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_async_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url"),
            timeout=kwargs.get("timeout"),
        ),
    )

    loader = RemoteCaseLoader(
        service_url="http://benchmark-service",
        dataset="alfworld",
        domain="all",
        split="train",
    )

    async def collect() -> list[list[object]]:
        return [batch async for batch in loader.batches()]

    assert asyncio.run(collect()) == []
    assert requests[0]["limit"] == DEFAULT_REMOTE_CASE_PAGE_SIZE == 200


def test_remote_case_loader_batch_size_overrides_default_page_size(monkeypatch):
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"cases": [], "next_cursor": None})

    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_async_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url"),
            timeout=kwargs.get("timeout"),
        ),
    )

    loader = RemoteCaseLoader(
        service_url="http://benchmark-service",
        dataset="alfworld",
        domain="all",
        split="train",
        batch_size=17,
    )

    async def collect() -> list[list[object]]:
        return [batch async for batch in loader.batches()]

    assert asyncio.run(collect()) == []
    assert requests[0]["limit"] == 17
