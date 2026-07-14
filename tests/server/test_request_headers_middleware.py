# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import importlib
import json
from typing import Any

import httpx
from fastapi import Request

from openviking.server.config import ServerConfig
from openviking.utils.request_headers import (
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
