# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from mcp.types import CallToolResult, TextContent, Tool

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import mcps as mcps_router_module
from openviking.service.mcp_service import MCPService, MCPToolMount
from openviking_cli.exceptions import InvalidArgumentError, ProcessingError, UnavailableError
from openviking_cli.session.user_id import UserIdentifier

pytestmark = pytest.mark.asyncio


def _ctx() -> RequestContext:
    return RequestContext(
        user=UserIdentifier("account-1", "user-1"),
        role=Role.USER,
    )


async def test_add_stores_headers_only_in_internal_registry(monkeypatch):
    service = MCPService(SimpleNamespace())
    registry = service._empty_document()
    writes: dict[str, dict] = {}

    monkeypatch.setattr(
        service,
        "_discover",
        AsyncMock(
            return_value=(
                SimpleNamespace(protocolVersion="2025-03-26"),
                [
                    Tool(
                        name="weather.current",
                        description="Current weather",
                        inputSchema={"type": "object"},
                    )
                ],
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_load",
        AsyncMock(return_value=registry),
    )
    publish = AsyncMock(return_value={"queue_status": "pending"})
    monkeypatch.setattr(service, "_publish_catalog", publish)

    async def capture_write(path, document):
        writes[path] = document

    monkeypatch.setattr(service, "_write_document", capture_write)

    result = await service.add(
        "weather",
        "https://mcp.example.com/api",
        {"Authorization": "Bearer secret"},
        _ctx(),
    )

    assert result["tool_count"] == 1
    assert result["header_names"] == ["Authorization"]
    assert result["processing"] == {"queue_status": "pending"}
    assert "headers" not in result
    publish.assert_awaited_once()
    assert publish.await_args.kwargs == {"wait": False}
    stored = writes["/local/account-1/_system/mcps.json"]["servers"]["weather"]
    assert "tools" not in stored
    assert "header_names" not in stored
    assert stored["headers"]["Authorization"] == "Bearer secret"
    assert list(writes) == ["/local/account-1/_system/mcps.json"]


async def test_publish_catalog_forwards_wait_to_batch_write(monkeypatch):
    batch_write = AsyncMock(return_value={"queue_status": "pending"})
    remove = AsyncMock()
    service = MCPService(SimpleNamespace(fs=SimpleNamespace(batch_write=batch_write, rm=remove)))
    monkeypatch.setattr(service, "_ensure_directory", AsyncMock())

    result = await service._publish_catalog(
        "demo",
        [
            {
                "server": "demo",
                "revision": "revision",
                "tool": {"name": "echo", "inputSchema": {"type": "object"}},
            }
        ],
        {"old"},
        _ctx(),
        wait=False,
    )

    assert result == {"queue_status": "pending"}
    batch_write.assert_awaited_once()
    assert batch_write.await_args.kwargs["wait"] is False
    remove.assert_awaited_once()
    assert remove.await_args.kwargs["wait"] is False


async def test_url_and_tool_filename_validation(monkeypatch):
    service = MCPService(SimpleNamespace())

    assert service._validate_url("http://mcp.example.com/api") == "http://mcp.example.com/api"
    with pytest.raises(InvalidArgumentError, match="HTTP or HTTPS"):
        service._validate_url("ftp://mcp.example.com/api")

    monkeypatch.setattr(
        service,
        "_discover",
        AsyncMock(
            return_value=(
                SimpleNamespace(protocolVersion="2025-03-26"),
                [Tool(name="..", inputSchema={"type": "object"})],
            )
        ),
    )
    monkeypatch.setattr(
        service,
        "_load",
        AsyncMock(return_value=service._empty_document()),
    )
    with pytest.raises(InvalidArgumentError, match="filenames"):
        await service.add("unsafe", "https://mcp.example.com/api", {}, _ctx())


async def test_validate_discovers_tools_without_registry_access(monkeypatch):
    service = MCPService(SimpleNamespace())
    server_info = SimpleNamespace(model_dump=lambda **_kwargs: {"name": "demo"})
    discover = AsyncMock(
        return_value=(
            SimpleNamespace(protocolVersion="2025-03-26", serverInfo=server_info),
            [Tool(name="echo", inputSchema={"type": "object"})],
        )
    )
    monkeypatch.setattr(service, "_discover", discover)
    load = AsyncMock()
    monkeypatch.setattr(service, "_load", load)

    result = await service.validate(
        "demo",
        "https://mcp.example.com/api",
        {"Authorization": "Bearer secret"},
    )

    assert result["valid"] is True
    assert result["tool_count"] == 1
    discover.assert_awaited_once_with(
        {"name": "demo", "url": "https://mcp.example.com/api"},
        {"Authorization": "Bearer secret"},
    )
    load.assert_not_awaited()


async def test_invoke_failure_is_not_retried_or_rewrapped(monkeypatch):
    service = MCPService(SimpleNamespace())
    server = {
        "name": "weather",
        "url": "https://mcp.example.com/api",
    }
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=(server, {})),
    )
    monkeypatch.setattr(
        service,
        "_catalog_names",
        AsyncMock(return_value={"current"}),
    )
    monkeypatch.setattr(
        service,
        "_tool",
        AsyncMock(
            return_value={
                "tool": {
                    "name": "current",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                }
            }
        ),
    )

    session = SimpleNamespace(
        initialize=AsyncMock(),
        call_tool=AsyncMock(side_effect=RuntimeError("connection dropped")),
    )

    @asynccontextmanager
    async def fake_session(_server, _headers, _timeout_seconds):
        try:
            yield session
        except (InvalidArgumentError, UnavailableError):
            raise

    monkeypatch.setattr(service, "_session", fake_session)

    with pytest.raises(UnavailableError, match="was not retried"):
        await service.invoke(
            "viking://agent/tools/weather/current.json",
            {"city": "Beijing"},
            _ctx(),
        )
    session.call_tool.assert_awaited_once_with(
        "current",
        arguments={"city": "Beijing"},
        read_timeout_seconds=timedelta(seconds=30),
    )


async def test_invoke_returns_upstream_result_without_routing_wrapper(monkeypatch):
    service = MCPService(SimpleNamespace())
    server = {"name": "demo", "url": "https://mcp.example.com/api"}
    monkeypatch.setattr(service, "_registered", AsyncMock(return_value=(server, {})))
    monkeypatch.setattr(service, "_catalog_names", AsyncMock(return_value={"get-sum"}))
    monkeypatch.setattr(
        service,
        "_tool",
        AsyncMock(
            return_value={
                "tool": {
                    "name": "get-sum",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "a": {"type": "number"},
                            "b": {"type": "number"},
                        },
                        "required": ["a", "b"],
                    },
                }
            }
        ),
    )
    upstream = CallToolResult(
        content=[TextContent(type="text", text="5")],
        structuredContent={"value": 5},
    )
    session = SimpleNamespace(initialize=AsyncMock(), call_tool=AsyncMock(return_value=upstream))

    @asynccontextmanager
    async def fake_session(_server, _headers, _timeout_seconds):
        yield session

    monkeypatch.setattr(service, "_session", fake_session)

    result = await service.invoke(
        "viking://agent/tools/demo/get-sum.json",
        {"a": 2, "b": 3},
        _ctx(),
        require_read_only=True,
    )

    assert result == {
        "content": [{"type": "text", "text": "5"}],
        "structuredContent": {"value": 5},
        "isError": False,
    }
    assert "uri" not in result
    assert "result" not in result


@pytest.mark.parametrize(
    ("annotations", "expected"),
    [
        (None, True),
        ({}, True),
        ({"readOnlyHint": True}, True),
        ({"readOnlyHint": False}, False),
        ({"destructiveHint": True}, False),
    ],
)
async def test_live_context_read_only_policy(annotations, expected):
    tool = {"name": "demo"}
    if annotations is not None:
        tool["annotations"] = annotations

    assert MCPService._tool_is_read_only({"tool": tool}) is expected


async def test_read_live_context_requires_explicit_invoke_for_write_tools(monkeypatch):
    service = MCPService(SimpleNamespace())
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=({"name": "demo", "url": "https://example.com/mcp"}, {})),
    )
    monkeypatch.setattr(service, "_catalog_names", AsyncMock(return_value={"write-record"}))
    monkeypatch.setattr(
        service,
        "_tool",
        AsyncMock(
            return_value={
                "tool": {
                    "name": "write-record",
                    "annotations": {
                        "readOnlyHint": False,
                        "destructiveHint": True,
                    },
                    "inputSchema": {"type": "object"},
                }
            }
        ),
    )
    open_session = MagicMock()
    monkeypatch.setattr(service, "_session", open_session)
    content = """---
type: OpenViking Live Context
x-openviking:
  type: mcp
  definition: viking://agent/tools/demo/write-record.json
  args:
    record: harmless
---
"""

    with pytest.raises(ProcessingError) as exc_info:
        await service.read_live_context(content, _ctx())

    message = str(exc_info.value)
    assert "Invoke the MCP tool explicitly" in message
    assert '"tool_uri": "viking://agent/tools/demo/write-record.json"' in message
    assert '"record": "harmless"' in message
    open_session.assert_not_called()


async def test_invoke_rejects_invalid_arguments_before_opening_session(monkeypatch):
    service = MCPService(SimpleNamespace())
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=({"name": "weather", "url": "https://example.com/mcp"}, {})),
    )
    monkeypatch.setattr(service, "_catalog_names", AsyncMock(return_value={"current"}))
    monkeypatch.setattr(
        service,
        "_tool",
        AsyncMock(
            return_value={
                "tool": {
                    "name": "current",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                        "required": ["city"],
                    },
                }
            }
        ),
    )
    open_session = MagicMock()
    monkeypatch.setattr(service, "_session", open_session)

    with pytest.raises(InvalidArgumentError, match="city"):
        await service.invoke(
            "viking://agent/tools/weather/current.json",
            {},
            _ctx(),
        )

    open_session.assert_not_called()


async def test_invoke_live_context_uses_definition_and_arguments(monkeypatch):
    service = MCPService(SimpleNamespace())
    invoke = AsyncMock(return_value={"content": [{"type": "text", "text": "5.858"}]})
    monkeypatch.setattr(service, "invoke", invoke)
    content = """---
type: OpenViking Live Context
title: Sum e and pi
description: Calculate the sum of e and pi.
x-openviking:
  schema_version: v1
  type: mcp
  definition: viking://agent/tools/demo/get-sum.json
  args:
    a: 3.14
    b: 2.718
  timeout_seconds: 120
---
"""

    result = await service.invoke_live_context(content, _ctx())

    assert result == {"content": [{"type": "text", "text": "5.858"}]}
    invoke.assert_awaited_once_with(
        "viking://agent/tools/demo/get-sum.json",
        {"a": 3.14, "b": 2.718},
        _ctx(),
        timeout_seconds=120,
    )


@pytest.mark.parametrize(
    ("mcp_result", "expected"),
    [
        (
            {
                "content": [{"type": "text", "text": '{"sum": 5.858}'}],
                "structuredContent": {"sum": 5.858},
                "isError": False,
            },
            '{"sum": 5.858}',
        ),
        (
            {
                "content": [
                    {"type": "text", "text": "first"},
                    {"type": "text", "text": "second"},
                ],
                "isError": False,
            },
            "first\nsecond",
        ),
        (
            {
                "structuredContent": {"sum": 5.858},
                "isError": False,
            },
            '{\n  "sum": 5.858\n}',
        ),
    ],
)
async def test_read_live_context_returns_resource_content(monkeypatch, mcp_result, expected):
    service = MCPService(SimpleNamespace())
    invoke = AsyncMock(return_value=mcp_result)
    monkeypatch.setattr(service, "invoke", invoke)
    content = """---
type: OpenViking Live Context
x-openviking:
  type: mcp
  definition: viking://agent/tools/demo/get-sum.json
  args: {}
---
"""

    assert await service.read_live_context(content, _ctx()) == expected
    invoke.assert_awaited_once_with(
        "viking://agent/tools/demo/get-sum.json",
        {},
        _ctx(),
        timeout_seconds=60,
        require_read_only=True,
    )


@pytest.mark.parametrize(
    "mcp_result",
    [
        {
            "content": [{"type": "text", "text": "upstream failed"}],
            "isError": True,
        },
        {
            "content": [{"type": "image", "data": "abc", "mimeType": "image/png"}],
            "isError": False,
        },
    ],
)
async def test_read_live_context_rejects_errors_and_non_text_results(monkeypatch, mcp_result):
    service = MCPService(SimpleNamespace())
    monkeypatch.setattr(service, "invoke", AsyncMock(return_value=mcp_result))
    content = """---
type: OpenViking Live Context
x-openviking:
  type: mcp
  definition: viking://agent/tools/demo/get-sum.json
  args: {}
---
"""

    with pytest.raises(ProcessingError):
        await service.read_live_context(content, _ctx())


async def test_invoke_live_context_ignores_regular_content():
    service = MCPService(SimpleNamespace())

    assert await service.invoke_live_context("# Regular document", _ctx()) is None
    assert await service.read_live_context("# Regular document", _ctx()) is None


@pytest.mark.parametrize("include_definition", [False, True])
async def test_find_uses_semantic_search_scoped_to_mcp_tools(monkeypatch, include_definition):
    search = AsyncMock(
        return_value={
            "resources": [
                {
                    "uri": "viking://agent/tools/weather/current.json",
                    "score": 0.87,
                    "abstract": "Live atmospheric conditions",
                }
            ]
        }
    )
    service = MCPService(SimpleNamespace(search=SimpleNamespace(find=search)))
    definition = {
        "name": "current",
        "description": "Get current weather conditions",
        "inputSchema": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
        "annotations": {"readOnlyHint": True},
        "execution": {"taskSupport": "forbidden"},
    }
    monkeypatch.setattr(
        service,
        "_tool",
        AsyncMock(
            return_value={
                "revision": "revision-1",
                "input_revision": "input-revision-1",
                "tool": definition,
            }
        ),
    )

    if include_definition:
        result = await service.find(
            "Should I carry an umbrella?", 5, _ctx(), include_definition=True
        )
    else:
        result = await service.find("Should I carry an umbrella?", 5, _ctx())

    search.assert_awaited_once_with(
        query="Should I carry an umbrella?",
        ctx=_ctx(),
        target_uri="viking://agent/tools",
        limit=5,
        level=[2],
    )
    assert result == {
        "tools": [
            {
                "uri": "viking://agent/tools/weather/current.json",
                "server": "weather",
                "name": "current",
                "description": "Get current weather conditions",
                "revision": "revision-1",
                "input_revision": "input-revision-1",
                "score": 0.87,
                **({"tool": definition} if include_definition else {}),
            }
        ],
        "total": 1,
    }


async def test_show_uses_catalog_instead_of_legacy_registry_tools(monkeypatch):
    service = MCPService(SimpleNamespace())
    monkeypatch.setattr(
        service,
        "_load",
        AsyncMock(
            return_value={
                "version": 1,
                "servers": {
                    "weather": {
                        "name": "weather",
                        "url": "https://mcp.example.com/api",
                        "tools": [{"tool": {"name": "legacy"}}],
                    },
                },
            }
        ),
    )
    current = {
        "uri": "viking://agent/tools/weather/current.json",
        "server": "weather",
        "revision": "revision-1",
        "input_revision": "input-revision-1",
        "tool": {"name": "current", "description": "Current weather"},
    }
    monkeypatch.setattr(service, "_catalog", AsyncMock(return_value=[current]))

    result = await service.show("weather", _ctx())

    assert result["tool_count"] == 1
    assert result["tools"] == [current]
    assert all(tool["tool"]["name"] != "legacy" for tool in result["tools"])


async def test_mount_list_and_unmount_persist_sqlite_database(monkeypatch):
    stored: dict[str, bytes] = {}

    async def read_storage(path):
        if path not in stored:
            raise FileNotFoundError(path)
        return stored[path]

    async def write_storage(path, content):
        stored[path] = content

    agfs = SimpleNamespace(
        read=AsyncMock(side_effect=read_storage),
        write=AsyncMock(side_effect=write_storage),
        ensure_parent_dirs=AsyncMock(),
    )
    fs = SimpleNamespace(
        write=AsyncMock(return_value={}),
        read=AsyncMock(),
        rm=AsyncMock(return_value={}),
        can_read_many=AsyncMock(
            side_effect=lambda uris, _ctx: dict.fromkeys(uris, True)
        ),
    )
    backing = SimpleNamespace(
        fs=fs,
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=False)),
    )
    service = MCPService(backing)
    monkeypatch.setattr(service, "_async_agfs", agfs)
    tool_document = {
        "server": "weather",
        "revision": "revision-1",
        "input_revision": "input-revision-1",
        "tool": {
            "name": "current",
            "title": "Current Weather",
            "description": "Get current weather",
            "annotations": {
                "readOnlyHint": False,
                "destructiveHint": True,
            },
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    }
    monkeypatch.setattr(service, "_tool", AsyncMock(return_value=tool_document))
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=({"name": "weather"}, {})),
    )

    tool_uri = "viking://agent/tools/weather/current.json"
    node_uri = "viking://resources/demo/weather.md"
    mounted = await service.mount(
        tool_uri,
        node_uri,
        "Beijing Weather",
        "Current weather in Beijing",
        60,
        {"city": "Beijing"},
        _ctx(),
        tags=["project=demo", "kind=live-context"],
        wait=True,
    )

    assert mounted["mounted"] is True
    assert "idempotent" not in mounted
    assert mounted["input_revision"] == "input-revision-1"
    written = fs.write.await_args.args[1]
    assert "type: OpenViking Live Context" in written
    assert "title: Beijing Weather" in written
    assert "description: Current weather in Beijing" in written
    assert f"definition: {tool_uri}" in written
    assert "input_revision: input-revision-1" in written
    assert "timeout_seconds: 60" in written
    assert "city: Beijing" in written
    assert "x-instruction" not in written
    fs.write.assert_awaited_once_with(
        node_uri,
        written,
        ctx=_ctx(),
        mode="replace",
        wait=True,
        tags=["project=demo", "kind=live-context"],
    )

    database = stored["/local/account-1/_system/live_context_nodes.dat"]
    connection = sqlite3.connect(":memory:")
    connection.deserialize(database)
    row = connection.execute("SELECT * FROM live_context_nodes").fetchone()
    connection.close()
    assert row[0] == node_uri
    assert row[1:4] == ("weather", "current", tool_uri)

    repeated = await service.mount(
        tool_uri,
        node_uri,
        "Shanghai Weather",
        "Current weather in Shanghai",
        120,
        {"city": "Shanghai"},
        _ctx(),
    )
    assert repeated["mounted"] is True
    assert "idempotent" not in repeated
    assert repeated["created_at"] == mounted["created_at"]
    assert fs.write.await_count == 2
    assert fs.write.await_args.kwargs["mode"] == "replace"
    updated = fs.write.await_args.args[1]
    assert "title: Shanghai Weather" in updated
    assert "city: Shanghai" in updated
    assert "timeout_seconds: 120" in updated
    assert fs.write.await_args.kwargs["wait"] is False

    listed = await service.mounts("weather", "current", _ctx())
    assert listed["total"] == 1
    assert listed["mounts"][0] == {
        "node_uri": node_uri,
        "mcp": "weather",
        "tool": "current",
        "tool_uri": tool_uri,
        "input_revision": "input-revision-1",
        "arguments": {"city": "Shanghai"},
        "title": "Shanghai Weather",
        "description": "Current weather in Shanghai",
        "timeout_seconds": 120,
        "tags": ["project=demo", "kind=live-context"],
        "created_at": mounted["created_at"],
    }
    assert repeated["tags"] == ["project=demo", "kind=live-context"]

    fs.read.return_value = updated
    unmounted = await service.unmount(node_uri, _ctx(), wait=True)
    assert unmounted["unmounted"] is True
    fs.rm.assert_awaited_once_with(node_uri, ctx=_ctx(), recursive=False, wait=True)
    assert (await service.mounts("weather", None, _ctx()))["mounts"] == []


async def test_mounts_only_returns_nodes_visible_to_caller(monkeypatch):
    visible_uri = "viking://resources/demo/shared.md"
    hidden_uri = "viking://user/alice/resources/private.md"
    can_read_many = AsyncMock(
        return_value={
            visible_uri: True,
            hidden_uri: False,
        }
    )
    service = MCPService(
        SimpleNamespace(fs=SimpleNamespace(can_read_many=can_read_many))
    )
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=({"name": "demo"}, {})),
    )

    def mount_database(_ctx):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            f"""
            CREATE TABLE live_context_nodes (
                node_uri TEXT PRIMARY KEY,
                mcp_name TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_uri TEXT NOT NULL,
                input_revision TEXT NOT NULL,
                args_json TEXT NOT NULL,
                data TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO live_context_nodes VALUES
                ('{visible_uri}', 'demo', 'current',
                 'viking://agent/tools/demo/current.json', 'visible-revision',
                 '{{"scope": "shared"}}',
                 '{{"title": "Shared", "timeout_seconds": 60}}',
                 '2026-10-23T00:00:00+00:00'),
                ('{hidden_uri}', 'demo', 'current',
                 'viking://agent/tools/demo/current.json', 'hidden-revision',
                 'not-json', 'not-json',
                 '2026-10-23T00:00:00+00:00');
            """
        )
        return connection

    monkeypatch.setattr(
        service,
        "_open_mount_database",
        AsyncMock(side_effect=mount_database),
    )

    result = await service.mounts("demo", "current", _ctx())

    assert result["total"] == 1
    assert result["mounts"][0]["node_uri"] == visible_uri
    assert result["mounts"][0]["arguments"] == {"scope": "shared"}
    checked_uris = can_read_many.await_args.args[0]
    assert set(checked_uris) == {visible_uri, hidden_uri}


async def test_batch_mount_and_unmount_share_filesystem_and_database_work(monkeypatch):
    stored: dict[str, bytes] = {}

    async def read_storage(path):
        if path not in stored:
            raise FileNotFoundError(path)
        return stored[path]

    async def write_storage(path, content):
        stored[path] = content

    agfs = SimpleNamespace(
        read=AsyncMock(side_effect=read_storage),
        write=AsyncMock(side_effect=write_storage),
        ensure_parent_dirs=AsyncMock(),
    )
    fs = SimpleNamespace(
        batch_write=AsyncMock(return_value={}),
        write=AsyncMock(return_value={}),
        read=AsyncMock(return_value="content"),
        rm=AsyncMock(return_value={}),
        mkdir=AsyncMock(return_value={}),
    )
    backing = SimpleNamespace(
        fs=fs,
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=True)),
    )
    service = MCPService(backing)
    monkeypatch.setattr(service, "_async_agfs", agfs)

    async def tool(tool_uri, _ctx):
        tool_name = tool_uri.rsplit("/", 1)[-1].removesuffix(".json")
        return {
            "server": "weather",
            "revision": f"revision-{tool_name}",
            "input_revision": f"input-revision-{tool_name}",
            "tool": {
                "name": tool_name,
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }

    tool_mock = AsyncMock(side_effect=tool)
    monkeypatch.setattr(service, "_tool", tool_mock)
    mounts = [
        MCPToolMount(
            tool_uri="viking://agent/tools/weather/current.json",
            node_uri="viking://resources/demo/current.md",
            title="Current weather",
            description=None,
            timeout_seconds=60,
            arguments={"city": "Beijing"},
            tags=["project=demo"],
        ),
        MCPToolMount(
            tool_uri="viking://agent/tools/weather/current.json",
            node_uri="viking://resources/demo/forecast.md",
            title="Weather forecast",
            description="Forecast",
            timeout_seconds=120,
            arguments={"city": "Shanghai"},
            tags=None,
        ),
    ]

    mounted = await service.batch_mount(mounts, _ctx(), wait=True)

    assert mounted["total"] == 2
    assert [item["node_uri"] for item in mounted["mounts"]] == [mount.node_uri for mount in mounts]
    assert mounted["mounts"][0]["tags"] == ["project=demo"]
    assert mounted["mounts"][1]["tags"] == []
    fs.batch_write.assert_awaited_once()
    batch_call = fs.batch_write.await_args.kwargs
    assert batch_call["root_uri"] == "viking://resources/demo"
    assert batch_call["wait"] is True
    assert batch_call["operations"][0]["tags"] == ["project=demo"]
    assert "tags" not in batch_call["operations"][1]
    assert fs.write.await_count == 0
    tool_mock.assert_awaited_once_with(
        "viking://agent/tools/weather/current.json",
        _ctx(),
    )

    database = stored["/local/account-1/_system/live_context_nodes.dat"]
    connection = sqlite3.connect(":memory:")
    connection.deserialize(database)
    rows = connection.execute(
        "SELECT node_uri FROM live_context_nodes ORDER BY node_uri"
    ).fetchall()
    connection.close()
    assert [row[0] for row in rows] == sorted(mount.node_uri for mount in mounts)

    fs.rm.side_effect = [{}, RuntimeError("delete failed")]
    with pytest.raises(RuntimeError, match="delete failed"):
        await service.batch_unmount(
            [mount.node_uri for mount in mounts],
            _ctx(),
        )
    fs.write.assert_awaited_once_with(
        mounts[0].node_uri,
        "content",
        ctx=_ctx(),
        mode="create",
        wait=True,
    )
    fs.rm.reset_mock(side_effect=True)
    fs.write.reset_mock()

    unmounted = await service.batch_unmount(
        [mount.node_uri for mount in mounts],
        _ctx(),
        wait=True,
    )

    assert unmounted["total"] == 2
    assert fs.rm.await_count == 2
    assert all(call.kwargs["wait"] is True for call in fs.rm.await_args_list)
    database = stored["/local/account-1/_system/live_context_nodes.dat"]
    connection = sqlite3.connect(":memory:")
    connection.deserialize(database)
    assert connection.execute("SELECT COUNT(*) FROM live_context_nodes").fetchone()[0] == 0
    connection.close()


async def test_mount_validates_node_uri_and_arguments(monkeypatch):
    fs = SimpleNamespace(write=AsyncMock(), rm=AsyncMock())
    backing = SimpleNamespace(
        fs=fs,
        viking_fs=SimpleNamespace(exists=AsyncMock(return_value=False)),
    )
    service = MCPService(backing)
    tool_document = {
        "server": "weather",
        "revision": "revision-1",
        "input_revision": "input-revision-1",
        "tool": {
            "name": "current",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
    monkeypatch.setattr(service, "_tool", AsyncMock(return_value=tool_document))

    def empty_mount_database(_ctx):
        connection = sqlite3.connect(":memory:")
        connection.execute(
            """
            CREATE TABLE live_context_nodes (
                node_uri TEXT PRIMARY KEY,
                mcp_name TEXT,
                tool_name TEXT,
                tool_uri TEXT,
                input_revision TEXT,
                args_json TEXT,
                created_at TEXT
            )
            """
        )
        return connection

    monkeypatch.setattr(
        service,
        "_open_mount_database",
        AsyncMock(side_effect=empty_mount_database),
    )

    with pytest.raises(InvalidArgumentError, match="title must not be empty"):
        await service.mount(
            "viking://agent/tools/weather/current.json",
            "viking://resources/demo/weather.md",
            " ",
            None,
            60,
            {"city": "Beijing"},
            _ctx(),
        )

    with pytest.raises(InvalidArgumentError, match=r"end with \.md"):
        await service.mount(
            "viking://agent/tools/weather/current.json",
            "viking://resources/demo/weather.json",
            "Weather",
            None,
            60,
            {"city": "Beijing"},
            _ctx(),
        )
    with pytest.raises(InvalidArgumentError, match="required property"):
        await service.mount(
            "viking://agent/tools/weather/current.json",
            "viking://resources/demo/weather.md",
            "Weather",
            None,
            60,
            {},
            _ctx(),
        )


async def test_sync_writes_changed_definitions_without_changing_input_revision(monkeypatch):
    service = MCPService(SimpleNamespace())
    server = {"name": "demo", "url": "https://mcp.example.com/api"}
    discovered = [
        Tool(name="added", inputSchema={"type": "object"}),
        Tool(
            name="changed",
            inputSchema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        ),
        Tool(
            name="definition-only",
            description="Updated description",
            inputSchema={"type": "object"},
            annotations={"readOnlyHint": True},
            outputSchema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
        ),
        Tool(name="unchanged", inputSchema={"type": "object"}),
    ]
    current = {
        document["tool"]["name"]: document
        for document in (service._tool_document(server, tool) for tool in discovered)
    }
    existing = [
        {
            "uri": "viking://agent/tools/demo/changed.json",
            **current["changed"],
            "revision": "old-changed-revision",
            "input_revision": "old-changed-input-revision",
        },
        {
            "uri": "viking://agent/tools/demo/definition-only.json",
            **current["definition-only"],
            "revision": "old-definition-revision",
            "tool": {
                **current["definition-only"]["tool"],
                "description": "Old description",
                "annotations": {"readOnlyHint": False},
                "outputSchema": {
                    "type": "object",
                    "properties": {"legacy": {"type": "boolean"}},
                },
            },
        },
        {
            "uri": "viking://agent/tools/demo/unchanged.json",
            **current["unchanged"],
        },
        {
            "uri": "viking://agent/tools/demo/removed.json",
            "server": "demo",
            "revision": "removed-revision",
            "input_revision": "removed-input-revision",
            "tool": {"name": "removed", "inputSchema": {"type": "object"}},
        },
    ]
    monkeypatch.setattr(
        service,
        "_registered",
        AsyncMock(return_value=(server, {"Authorization": "secret"})),
    )
    monkeypatch.setattr(
        service,
        "_discover",
        AsyncMock(
            return_value=(
                SimpleNamespace(protocolVersion="2025-03-26"),
                discovered,
            )
        ),
    )
    monkeypatch.setattr(service, "_catalog", AsyncMock(return_value=existing))
    publish = AsyncMock()
    monkeypatch.setattr(service, "_publish_catalog", publish)
    affected = [
        {
            "node_uri": "viking://resources/demo/changed.md",
            "tool_uri": "viking://agent/tools/demo/changed.json",
            "tool": "changed",
            "status": "REVIEW",
        }
    ]
    monkeypatch.setattr(service, "_affected_mounts", AsyncMock(return_value=affected))
    registry = {
        "version": 1,
        "servers": {
            "demo": {
                "name": "demo",
                "url": "https://mcp.example.com/api",
                "headers": {},
            }
        },
    }
    monkeypatch.setattr(service, "_load", AsyncMock(return_value=registry))
    monkeypatch.setattr(service, "_write_document", AsyncMock())

    result = await service.sync("demo", _ctx(), wait=True)

    assert existing[1]["input_revision"] == current["definition-only"]["input_revision"]
    assert existing[1]["revision"] != current["definition-only"]["revision"]
    assert result["changes"] == {
        "added": ["added"],
        "changed": ["changed"],
        "unchanged": ["definition-only", "unchanged"],
        "removed": ["removed"],
    }
    assert result["affected_mounts"] == affected
    published = publish.await_args.args[1]
    assert [document["tool"]["name"] for document in published] == [
        "added",
        "changed",
        "definition-only",
    ]
    publish.assert_awaited_once_with(
        "demo",
        published,
        {"removed"},
        _ctx(),
        wait=True,
    )


async def test_affected_mounts_are_recomputed_without_updating_revisions(monkeypatch):
    hidden_uri = "viking://user/alice/resources/private.md"
    can_read_many = AsyncMock(
        side_effect=lambda uris, _ctx: {
            uri: uri != hidden_uri for uri in uris
        }
    )
    service = MCPService(
        SimpleNamespace(fs=SimpleNamespace(can_read_many=can_read_many))
    )

    def mount_database(_ctx):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE live_context_nodes (
                node_uri TEXT PRIMARY KEY,
                mcp_name TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                tool_uri TEXT NOT NULL,
                input_revision TEXT NOT NULL,
                args_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO live_context_nodes VALUES
                ('viking://resources/demo/valid.md', 'demo', 'valid',
                 'viking://agent/tools/demo/valid.json', 'old-valid',
                 '{"city": "Beijing"}', '2026-10-09T00:00:00+00:00'),
                ('viking://resources/demo/invalid.md', 'demo', 'invalid',
                 'viking://agent/tools/demo/invalid.json', 'old-invalid',
                 '{"city": "Beijing"}', '2026-10-09T00:00:00+00:00'),
                ('viking://resources/demo/removed.md', 'demo', 'removed',
                 'viking://agent/tools/demo/removed.json', 'old-removed',
                 '{}', '2026-10-09T00:00:00+00:00'),
                ('viking://resources/demo/current.md', 'demo', 'current',
                 'viking://agent/tools/demo/current.json', 'current-revision',
                 '{}', '2026-10-09T00:00:00+00:00'),
                ('viking://user/alice/resources/private.md', 'demo', 'private',
                 'viking://agent/tools/demo/private.json', 'old-private',
                 '{}', '2026-10-09T00:00:00+00:00');
            """
        )
        return connection

    monkeypatch.setattr(
        service,
        "_open_mount_database",
        AsyncMock(side_effect=mount_database),
    )
    current_tools = {
        "valid": {
            "input_revision": "new-valid",
            "tool": {
                "name": "valid",
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        },
        "invalid": {
            "input_revision": "new-invalid",
            "tool": {
                "name": "invalid",
                "inputSchema": {
                    "type": "object",
                    "properties": {"unit": {"type": "string"}},
                    "required": ["unit"],
                },
            },
        },
        "current": {
            "input_revision": "current-revision",
            "tool": {"name": "current", "inputSchema": {"type": "object"}},
        },
    }

    first = await service._affected_mounts("demo", current_tools, _ctx())
    second = await service._affected_mounts("demo", current_tools, _ctx())

    assert second == first
    assert {item["tool"]: item["status"] for item in first} == {
        "invalid": "INVALID",
        "removed": "REMOVED",
        "valid": "REVIEW",
    }
    invalid = next(item for item in first if item["tool"] == "invalid")
    assert "required property" in invalid["reason"]
    assert invalid["mounted_input_revision"] == "old-invalid"
    assert invalid["current_input_revision"] == "new-invalid"
    assert all(item["tool"] != "current" for item in first)
    assert all(item["tool"] != "private" for item in first)
    assert can_read_many.await_count == 2


async def test_router_exposes_and_delegates_only_requested_operations(monkeypatch):
    methods = {
        name: AsyncMock(return_value={"operation": name})
        for name in (
            "add",
            "remove",
            "list",
            "find",
            "validate",
            "show",
            "sync",
            "status",
            "invoke",
            "mount",
            "batch_mount",
            "unmount",
            "batch_unmount",
            "mounts",
        )
    }
    fake_service = SimpleNamespace(mcps=SimpleNamespace(**methods))
    monkeypatch.setattr(mcps_router_module, "get_service", lambda: fake_service)

    app = FastAPI()
    app.include_router(mcps_router_module.router)
    app.dependency_overrides[get_request_context] = _ctx
    transport = httpx.ASGITransport(app=app)

    requests = [
        (
            "POST",
            "/api/v1/mcps",
            {"name": "demo", "url": "https://example.com/mcp", "wait": True},
        ),
        ("DELETE", "/api/v1/mcps/demo", None),
        ("GET", "/api/v1/mcps", None),
        ("POST", "/api/v1/mcps/find", {"query": "weather"}),
        (
            "POST",
            "/api/v1/mcps/validate",
            {"name": "demo", "url": "https://example.com/mcp"},
        ),
        ("GET", "/api/v1/mcps/demo", None),
        ("POST", "/api/v1/mcps/demo/sync", {"wait": True}),
        ("GET", "/api/v1/mcps/status?name=demo", None),
        (
            "POST",
            "/api/v1/mcps/invoke",
            {
                "uri": "viking://agent/tools/demo/current.json",
                "arguments": {"city": "Beijing"},
            },
        ),
        (
            "POST",
            "/api/v1/mcps/mount",
            {
                "tool_uri": "viking://agent/tools/demo/current.json",
                "node_uri": "viking://resources/demo/current.md",
                "title": "Current Weather",
                "description": "Current weather in Beijing",
                "timeout_seconds": 120,
                "arguments": {"city": "Beijing"},
                "tags": ["project=demo"],
                "wait": True,
            },
        ),
        (
            "POST",
            "/api/v1/mcps/batch-mount",
            {
                "mounts": [
                    {
                        "tool_uri": "viking://agent/tools/demo/current.json",
                        "node_uri": "viking://resources/demo/batch.md",
                        "title": "Batch Weather",
                    }
                ],
                "wait": True,
            },
        ),
        (
            "DELETE",
            "/api/v1/mcps/mount?node_uri=viking%3A%2F%2Fresources%2Fdemo%2Fcurrent.md&wait=true",
            None,
        ),
        (
            "POST",
            "/api/v1/mcps/batch-unmount",
            {
                "node_uris": ["viking://resources/demo/batch.md"],
                "wait": True,
            },
        ),
        ("GET", "/api/v1/mcps/mounts?mcp=demo&tool=current", None),
    ]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path, body in requests:
            response = await client.request(method, path, json=body)
            assert response.status_code == 200, response.text

    assert all(mock.await_count == 1 for mock in methods.values())
    methods["add"].assert_awaited_once_with(
        "demo",
        "https://example.com/mcp",
        {},
        _ctx(),
        wait=True,
    )
    methods["mount"].assert_awaited_once_with(
        "viking://agent/tools/demo/current.json",
        "viking://resources/demo/current.md",
        "Current Weather",
        "Current weather in Beijing",
        120,
        {"city": "Beijing"},
        _ctx(),
        tags=["project=demo"],
        wait=True,
    )
    methods["find"].assert_awaited_once_with(
        "weather",
        10,
        _ctx(),
        include_definition=True,
    )
    methods["sync"].assert_awaited_once_with("demo", _ctx(), wait=True)
    methods["batch_mount"].assert_awaited_once_with(
        [
            MCPToolMount(
                tool_uri="viking://agent/tools/demo/current.json",
                node_uri="viking://resources/demo/batch.md",
                title="Batch Weather",
                description=None,
                timeout_seconds=60,
                arguments={},
                tags=None,
            )
        ],
        _ctx(),
        wait=True,
    )
    methods["unmount"].assert_awaited_once_with(
        "viking://resources/demo/current.md",
        _ctx(),
        wait=True,
    )
    methods["batch_unmount"].assert_awaited_once_with(
        ["viking://resources/demo/batch.md"],
        _ctx(),
        wait=True,
    )
