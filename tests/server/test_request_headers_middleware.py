# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
from fastapi import Request

from openviking.server.config import ServerConfig
from openviking.storage.queuefs.named_queue import DequeueHandlerBase, NamedQueue
from openviking.utils.request_headers import (
    MODEL_REQUEST_CONTEXT_KEY,
    create_task_with_request_headers,
    get_request_headers_snapshot,
    resolve_extra_headers,
)

CONFIGURED_HEADERS = {
    "X-Static": "fixed",
    "Authorization": "@request.header.Authorization",
    "X-Upstream-Tenant": "@request.header.X-Tenant",
}


def _relevant_snapshot() -> dict[str, str]:
    snapshot = get_request_headers_snapshot() or {}
    return {name: snapshot[name] for name in ("authorization", "x-tenant") if name in snapshot}


class _CapturingMCPApp:
    def __init__(self, snapshots: list[dict[str, str]]) -> None:
        self._snapshots = snapshots

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        snapshot = _relevant_snapshot()
        self._snapshots.append(snapshot)
        body = json.dumps(
            {
                "snapshot": snapshot,
                "resolved": resolve_extra_headers(CONFIGURED_HEADERS),
            }
        ).encode()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": body})


async def test_request_header_context_covers_rest_and_mcp_and_resets(monkeypatch) -> None:
    app_module = importlib.import_module("openviking.server.app")
    mcp_module = importlib.import_module("openviking.server.mcp_endpoint")
    mcp_snapshots: list[dict[str, str]] = []
    monkeypatch.setattr(
        mcp_module,
        "create_mcp_app",
        lambda: _CapturingMCPApp(mcp_snapshots),
    )
    monkeypatch.setattr(
        app_module,
        "collect_dynamic_request_header_names",
        lambda *configs: {"Authorization", "X-Tenant"},
    )

    app = app_module.create_app(config=ServerConfig(), service=object())

    @app.get("/_test/request-headers")
    async def inspect_request_headers(_request: Request) -> dict[str, object]:
        return {
            "snapshot": _relevant_snapshot(),
            "resolved": resolve_extra_headers(CONFIGURED_HEADERS),
        }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rest_response = await client.get(
            "/_test/request-headers",
            headers={"X-Tenant": "rest-tenant", "Authorization": "Bearer rest"},
        )
        mcp_response = await client.post(
            "/mcp",
            headers={"X-Tenant": "mcp-tenant", "Authorization": "Bearer mcp"},
        )

    assert rest_response.json() == {
        "snapshot": {"authorization": "Bearer rest", "x-tenant": "rest-tenant"},
        "resolved": {
            "X-Static": "fixed",
            "Authorization": "Bearer rest",
            "X-Upstream-Tenant": "rest-tenant",
        },
    }
    assert mcp_response.json() == {
        "snapshot": {"authorization": "Bearer mcp", "x-tenant": "mcp-tenant"},
        "resolved": {
            "X-Static": "fixed",
            "Authorization": "Bearer mcp",
            "X-Upstream-Tenant": "mcp-tenant",
        },
    }
    assert mcp_snapshots == [{"authorization": "Bearer mcp", "x-tenant": "mcp-tenant"}]
    assert get_request_headers_snapshot() is None


async def test_http_background_task_hands_headers_to_queue_worker(monkeypatch) -> None:
    app_module = importlib.import_module("openviking.server.app")
    monkeypatch.setattr(
        app_module,
        "collect_dynamic_request_header_names",
        lambda *configs: {"Authorization", "X-Tenant"},
    )
    app = app_module.create_app(config=ServerConfig(), service=object())
    release = asyncio.Event()
    tasks = []
    worker_results = []

    class Handler(DequeueHandlerBase):
        async def on_dequeue(self, data):
            worker_results.append(
                {
                    "snapshot": dict(get_request_headers_snapshot() or {}),
                    "resolved": resolve_extra_headers(CONFIGURED_HEADERS),
                    "data": data,
                }
            )
            self.report_success()
            return data

    queue = NamedQueue(
        MagicMock(),
        "/queue",
        "Semantic",
        dequeue_handler=Handler(),
        propagate_model_request_context=True,
    )
    queue._initialized = True
    queue._async_agfs = MagicMock()
    queue._async_agfs.write = AsyncMock(return_value="queue-id")

    @app.post("/_test/background-model")
    async def start_background_model_task() -> dict[str, str]:
        async def enqueue_after_response() -> None:
            await release.wait()
            await queue.enqueue({"uri": "viking://resources/repo"})

        tasks.append(create_task_with_request_headers(enqueue_after_response()))
        return {"status": "accepted"}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/_test/background-model",
            headers={
                "Authorization": "Bearer user-a",
                "X-Tenant": "tenant-a",
                "Cookie": "must-not-persist",
            },
        )

    assert response.json() == {"status": "accepted"}
    assert get_request_headers_snapshot() is None
    release.set()
    await tasks[0]

    persisted = queue._async_agfs.write.await_args.args[1].decode()
    assert "must-not-persist" not in persisted
    assert MODEL_REQUEST_CONTEXT_KEY in persisted
    queued = {"id": "queue-id", "data": persisted}

    def run_worker() -> None:
        queue._on_dequeue_start()
        asyncio.run(queue.process_dequeued(queued))

    await asyncio.to_thread(run_worker)

    assert worker_results == [
        {
            "snapshot": {"authorization": "Bearer user-a", "x-tenant": "tenant-a"},
            "resolved": {
                "X-Static": "fixed",
                "Authorization": "Bearer user-a",
                "X-Upstream-Tenant": "tenant-a",
            },
            "data": {"id": "queue-id", "data": json.dumps({"uri": "viking://resources/repo"})},
        }
    ]
