# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP add_resource parse-mode propagation tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server import mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking.server.upload_token_store import upload_token_store
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def mcp_context(monkeypatch: pytest.MonkeyPatch):
    ctx = RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )
    token = mcp_endpoint._mcp_ctx.set(ctx)
    service = SimpleNamespace(
        resources=SimpleNamespace(
            add_resource=AsyncMock(
                return_value={"root_uri": "viking://resources/test", "task_id": "task-1"}
            )
        )
    )
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    upload_token_store.clear()
    yield service
    upload_token_store.clear()
    mcp_endpoint._mcp_ctx.reset(token)


@pytest.mark.asyncio
async def test_mcp_add_resource_schema_exposes_parse_mode_only_through_args(mcp_context):
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    schema = tools["add_resource"].inputSchema

    assert "parse_mode" not in schema["properties"]
    assert "args" in schema["properties"]


@pytest.mark.asyncio
async def test_mcp_remote_add_forwards_no_split(
    monkeypatch: pytest.MonkeyPatch,
    mcp_context,
):
    monkeypatch.setattr(mcp_endpoint, "_maybe_sitemap_hint", AsyncMock(return_value=""))

    result = await mcp_endpoint.add_resource(
        path="https://example.com/manual.pdf",
        args={"parse_mode": "no_split"},
    )

    assert "Resource added" in result
    assert mcp_context.resources.add_resource.await_args.kwargs["args"] == {
        "parse_mode": "no_split"
    }


@pytest.mark.asyncio
async def test_mcp_temp_file_id_forwards_no_split(
    monkeypatch: pytest.MonkeyPatch,
    mcp_context,
):
    ingest = AsyncMock(return_value={"root_uri": "viking://resources/upload"})
    monkeypatch.setattr(mcp_endpoint, "ingest_temp_upload", ingest)
    monkeypatch.setattr(
        mcp_endpoint.TempUploadStore,
        "build",
        lambda _config: object(),
    )

    result = await mcp_endpoint.add_resource(
        temp_file_id="upload_abcdef123.md",
        args={"parse_mode": "no_split"},
    )

    assert "Resource added" in result
    assert ingest.await_args.kwargs["args"] == {"parse_mode": "no_split"}


@pytest.mark.asyncio
async def test_mcp_local_upload_token_binds_no_split(
    monkeypatch: pytest.MonkeyPatch,
    mcp_context,
):
    monkeypatch.setattr(
        mcp_endpoint,
        "_resolve_public_base_url",
        lambda: ("https://ov.example.com", "config"),
    )

    await mcp_endpoint.add_resource(
        path="/tmp/manual.pdf",
        args={"parse_mode": "no_split"},
    )

    assert len(upload_token_store._store) == 1
    token_info = next(iter(upload_token_store._store.values()))
    assert token_info.parse_mode == "no_split"


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_parse_mode(mcp_context):
    result = await mcp_endpoint.add_resource(
        path="https://example.com/manual.pdf",
        args={"parse_mode": "unsupported"},
    )

    assert "Error" in result
    assert "parse_mode" in result
