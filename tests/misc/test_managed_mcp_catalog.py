# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for managed MCP catalog write boundaries."""

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from mcp.types import CallToolResult, Tool

from openviking.core.namespace_policy import ensure_mcp_catalog_access
from openviking.server.identity import RequestContext, Role
from openviking.service.mcp_service import MCPService
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)
USER_ROOT = "viking://user/alice/tools"
AGENT_ROOT = "viking://agent/tools"


class Page:
    def __init__(self, entries):
        self.entries = entries
        self.has_more = False


class FakeAGFS:
    def __init__(self):
        self.files = {}

    async def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write(self, path, payload):
        self.files[path] = bytes(payload)

    async def ensure_parent_dirs(self, path):
        return None


class FakeFS:
    def __init__(self):
        self.files = {}
        self.dirs = set()

    async def exists(self, uri, ctx=None):
        return uri in self.dirs or any(path.startswith(uri + "/") for path in self.files)

    async def mkdir(self, uri, ctx=None, **kwargs):
        ensure_mcp_catalog_access(uri, ctx, include_descendants=True)
        self.dirs.add(uri)

    async def batch_write(self, root_uri, operations, ctx=None, wait=True):
        for operation in operations:
            ensure_mcp_catalog_access(operation["uri"], ctx)
            self.files[operation["uri"]] = operation["content"]
        return {"written": len(operations)}

    async def read(self, uri, ctx=None):
        return self.files[uri]

    async def rm(self, uri, ctx=None, recursive=False, wait=True):
        ensure_mcp_catalog_access(uri, ctx, include_descendants=recursive)
        for path in list(self.files):
            if path == uri or path.startswith(uri + "/"):
                del self.files[path]

    async def ls(self, uri, ctx=None, output="agent", node_limit=1000, offset=0):
        entries = [
            {"uri": path, "isDir": False}
            for path in sorted(self.files)
            if path.startswith(uri + "/")
        ]
        return Page(entries[offset : offset + node_limit])


class FakeService:
    def __init__(self):
        self.fs = FakeFS()
        self.viking_fs = self.fs
        self._agfs = FakeAGFS()


def _make_service():
    fakes = FakeService()
    service = MCPService(fakes)
    service._async_agfs = fakes._agfs
    tool = Tool(
        name="weather",
        inputSchema={
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    )
    service._discover = AsyncMock(
        return_value=(SimpleNamespace(protocolVersion="2025-03-26"), [tool])
    )

    @asynccontextmanager
    async def session(_server, _headers, _timeout_seconds):
        upstream = SimpleNamespace(
            initialize=AsyncMock(),
            call_tool=AsyncMock(
                return_value=CallToolResult(content=[], structuredContent={"temperature": 23})
            ),
        )
        yield upstream

    service._session = session
    return service, fakes


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_root", [USER_ROOT, AGENT_ROOT])
async def test_managed_catalog_service_can_publish_sync_and_remove(tools_root: str) -> None:
    service, fakes = _make_service()

    await service.add(
        "weather",
        "https://mcp.example.invalid",
        {},
        CTX,
        target_uri=tools_root,
        wait=True,
    )
    tool_uri = f"{tools_root}/weather/weather.json"
    assert tool_uri in fakes.fs.files

    await service.sync("weather", CTX, target_uri=tools_root, wait=True)
    await service.remove("weather", CTX, target_uri=tools_root)

    assert not any(path.startswith(f"{tools_root}/weather/") for path in fakes.fs.files)


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
@pytest.mark.parametrize("uri", [USER_ROOT, AGENT_ROOT])
def test_managed_catalog_rejects_ordinary_writes_for_all_roles(role: Role, uri: str) -> None:
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=role)
    with pytest.raises(PermissionDeniedError, match="Use MCP management"):
        ensure_mcp_catalog_access(f"{uri}/weather/weather.json", ctx)


@pytest.mark.parametrize(
    "uri",
    ["viking://user/alice", "viking://agent", "viking://agent/tools/"],
)
def test_managed_catalog_descendant_mutations_are_rejected(uri: str) -> None:
    with pytest.raises(PermissionDeniedError, match="Use MCP management"):
        ensure_mcp_catalog_access(uri, CTX, include_descendants=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("tools_root", [USER_ROOT, AGENT_ROOT])
async def test_generic_catalog_writes_are_denied(tools_root: str) -> None:
    service, fakes = _make_service()

    with pytest.raises(PermissionDeniedError, match="Use MCP management"):
        await fakes.fs.batch_write(
            tools_root,
            [{"uri": f"{tools_root}/weather/weather.json", "content": json.dumps({"tool": {}})}],
            ctx=CTX,
        )

    with pytest.raises(PermissionDeniedError, match="Use MCP management"):
        await fakes.fs.rm(tools_root, ctx=CTX, recursive=True)


@pytest.mark.asyncio
async def test_fs_rm_route_rejects_managed_catalog(monkeypatch):
    from openviking.server.routers import filesystem

    class GuardFS:
        async def rm(self, uri, ctx=None, recursive=False, wait=False):
            ensure_mcp_catalog_access(uri, ctx, include_descendants=recursive)

    monkeypatch.setattr(
        filesystem,
        "get_service",
        lambda: SimpleNamespace(fs=GuardFS()),
    )

    with pytest.raises(PermissionDeniedError, match="Use MCP management") as raised:
        await filesystem.rm(
            uri="viking://agent/tools/weather",
            recursive=True,
            wait=False,
            _ctx=CTX,
        )
    from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS

    assert raised.value.code == "PERMISSION_DENIED"
    assert ERROR_CODE_TO_HTTP_STATUS["PERMISSION_DENIED"] == 403


@pytest.mark.asyncio
async def test_content_write_route_rejects_managed_catalog(monkeypatch):
    from openviking.server.routers import content
    from openviking.server.routers.content import WriteContentRequest

    class GuardFS:
        async def write(self, uri, content, ctx=None, **kwargs):
            ensure_mcp_catalog_access(uri, ctx)

    monkeypatch.setattr(
        content,
        "get_service",
        lambda: SimpleNamespace(fs=GuardFS()),
    )

    with pytest.raises(PermissionDeniedError, match="Use MCP management"):
        await content.write(
            WriteContentRequest(
                uri="viking://agent/tools/weather/current.json",
                content="{}",
            ),
            _ctx=CTX,
        )
