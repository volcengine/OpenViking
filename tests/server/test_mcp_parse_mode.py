# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP add_resource parse-mode contracts."""

from unittest.mock import AsyncMock

import pytest

from openviking.parse.accessors.base import LocalResource, SourceType
from openviking.parse.accessors.http_accessor import HTTPAccessor
from openviking.server import mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking.server.upload_token_store import upload_token_store
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_resources import _wait_task_terminal


@pytest.fixture(autouse=True)
def mcp_context(monkeypatch: pytest.MonkeyPatch):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role(Role.USER),
    )
    token = mcp_endpoint._mcp_ctx.set(ctx)
    monkeypatch.setattr(mcp_endpoint, "get_service", object)
    upload_token_store.clear()
    try:
        yield
    finally:
        upload_token_store.clear()
        mcp_endpoint._mcp_ctx.reset(token)


async def _assert_imported_file(client, uri, content):
    response = await client.get("/api/v1/tasks", params={"task_type": "add_resource"})
    assert response.status_code == 200, response.text
    [submitted] = response.json()["result"]
    task = await _wait_task_terminal(client, submitted["task_id"])
    assert task["status"] == "completed", task
    assert task["result"]["root_uri"] == uri
    read = await client.get("/api/v1/content/read", params={"uri": uri})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content.strip()


@pytest.mark.asyncio
async def test_mcp_add_resource_schema_exposes_parse_mode_only_through_args(mcp_context):
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    schema = tools["add_resource"].inputSchema

    assert "parse_mode" not in schema["properties"]
    assert "args" in schema["properties"]
    assert "create_parent" not in schema["properties"]


@pytest.mark.usefixtures("public_resource_dns")
async def test_mcp_remote_add_forwards_no_split(client, service, tmp_path, monkeypatch):
    source = "https://example.com/manual.md"
    downloaded = tmp_path / "manual.md"
    content = "# Install\n\nFirst step.\n\n# Configure\n\nSecond step.\n"
    downloaded.write_text(content)
    uri = "viking://resources/manual.md"

    async def download(self, path, **kwargs):
        assert path == source
        return LocalResource(
            path=downloaded,
            source_type=SourceType.HTTP,
            original_source=source,
            meta={"original_filename": downloaded.name},
        )

    monkeypatch.setattr(HTTPAccessor, "access", download)
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(mcp_endpoint, "_maybe_sitemap_hint", AsyncMock(return_value=""))

    response = await mcp_endpoint.add_resource(
        path=source, parent="viking://resources", args={"parse_mode": "no_split"}
    )
    assert "processing in background" in response, response
    await _assert_imported_file(client, uri, content)


async def test_mcp_temp_file_id_forwards_no_split(client, service, upload_temp_dir, monkeypatch):
    content = "# Usage\n\nRun once.\n\n# Cleanup\n\nRemove temporary files.\n"
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("manual.md", content, "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    temp_file_id = uploaded.json()["result"]["temp_file_id"]
    uri = "viking://resources/manual.md"
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)

    response = await mcp_endpoint.add_resource(
        temp_file_id=temp_file_id, parent="viking://resources", args={"parse_mode": "no_split"}
    )
    assert "processing in background" in response, response
    await _assert_imported_file(client, uri, content)


@pytest.mark.asyncio
async def test_mcp_rejects_to_and_parent_together(mcp_context):
    result = await mcp_endpoint.add_resource(
        path="/tmp/manual.pdf",
        to="viking://resources/exact-target",
        parent="viking://~/resources/team",
    )

    assert result == "Error: Cannot specify both 'to' and 'parent' at the same time."
    assert upload_token_store._store == {}


@pytest.mark.asyncio
async def test_mcp_rejects_invalid_parse_mode(mcp_context):
    result = await mcp_endpoint.add_resource(
        path="https://example.com/manual.pdf",
        args={"parse_mode": "unsupported"},
    )

    assert "Error" in result
    assert "parse_mode" in result
