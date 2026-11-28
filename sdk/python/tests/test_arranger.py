# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import json

import httpx
import pytest
from openviking_sdk import AsyncContextArranger, AsyncHTTPClient
from openviking_sdk.arranger import ContextChangeAction


@pytest.mark.asyncio
async def test_arranger_rediscovers_deeply_nested_owned_nodes(sdk_transport):
    root = "viking://resources/arrange"
    path = "region/country/city/area/weather.md"
    node = f"{root}/{path}"
    mount = None

    def handle(request):
        nonlocal mount
        endpoint = request.url.path
        if endpoint in {"/api/v1/fs/ls", "/api/v1/fs/tree"}:
            depth = int(request.url.params.get("level_limit", "3"))
            entries = []
            if mount is not None and depth >= path.count("/") + 1:
                assert request.url.params.get_list("tags") == ["ov-cac-owner=weather"]
                entries.append({"uri": node, "tags": mount["tags"], "isDir": False})
            return httpx.Response(200, json={"status": "ok", "result": entries, "has_more": False})
        if endpoint == "/api/v1/fs/stat":
            if mount is None:
                return httpx.Response(404, json={"status": "error", "error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, json={"status": "ok", "result": {"uri": node}})
        if endpoint == "/api/v1/mcps/batch-mount":
            (mount,) = json.loads(request.content)["mounts"]
            return httpx.Response(200, json={"status": "ok", "result": {"succeeded": [node]}})
        if endpoint == "/api/v1/mcps/mounts":
            return httpx.Response(200, json={"status": "ok", "result": {"mounts": [mount]}})
        raise AssertionError(f"Unexpected SDK request: {endpoint}")

    sdk_transport(handle)
    client = AsyncHTTPClient(url="http://localhost:1933")
    await client.initialize()
    try:
        arranger = AsyncContextArranger(client, "weather", root)
        arranger.live_context(path, "viking://agent/tools/weather/current.json", "Weather")
        assert [change.action for change in (await arranger.apply()).applied] == [
            ContextChangeAction.CREATE
        ]
        assert [change.action for change in (await arranger.plan()).changes] == [
            ContextChangeAction.UNCHANGED
        ]
        empty = AsyncContextArranger(client, "weather", root)
        assert [change.action for change in (await empty.plan()).changes] == [
            ContextChangeAction.DELETE
        ]
    finally:
        await client.close()
