# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""MCP metadata updates must use the same storage contract as HTTP."""

import pytest
import pytest_asyncio
from mcp.types import CallToolRequest, CallToolRequestParams

from openviking.models.embedder.base import DenseEmbedderBase, EmbedResult
from openviking.server import mcp_endpoint
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking.storage import viking_fs as viking_fs_module
from openviking.storage.vector_ids import vector_record_id
from openviking.utils.time_utils import get_current_timestamp
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


@pytest_asyncio.fixture
async def service(tmp_path, monkeypatch):
    class FakeEmbedder(DenseEmbedderBase):
        def __init__(self):
            super().__init__(model_name="test-embedder")

        def embed(self, text, is_query=False):
            return EmbedResult(dense_vector=[0.1] * 4)

        def get_dimension(self):
            return 4

    monkeypatch.setattr(EmbeddingConfig, "get_embedder", lambda self: FakeEmbedder())
    # Use native local storage; the global service fixture's MockLocalAGFS
    # does not implement the leadership operations required during startup.
    previous_fs = getattr(viking_fs_module, "_instance", None)
    OpenVikingConfigSingleton.reset_instance()
    OpenVikingConfigSingleton.initialize(
        config_dict={
            "storage": {
                "workspace": str(tmp_path),
                "agfs": {"backend": "local"},
                "vectordb": {"backend": "local"},
            },
            "embedding": {
                "dense": {
                    "provider": "openai",
                    "model": "test-embedder",
                    "api_key": "test-key",
                    "dimension": 4,
                }
            },
        }
    )
    instance = OpenVikingService(path=str(tmp_path), user=UserIdentifier.the_default_user())
    try:
        await instance.initialize()
        yield instance
    finally:
        await instance.close()
        viking_fs_module._instance = previous_fs  # type: ignore[attr-defined]
        OpenVikingConfigSingleton.reset_instance()


@pytest.mark.asyncio
async def test_mcp_set_tags_preserves_content_and_updates_retrieval_metadata(service):
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    assert "set_tags" in tools, "MCP must expose metadata-only tag updates"
    assert tools["set_tags"].annotations is not None
    assert tools["set_tags"].annotations.readOnlyHint is False
    ctx = RequestContext(user=service.user, role=Role(Role.USER))
    root = f"viking://user/{ctx.user.user_id}/resources/mcp-tags"
    file_uri = f"{root}/note.md"
    await service.viking_fs.write_file(file_uri, "unchanged body", ctx=ctx)
    record_id = vector_record_id(ctx.account_id, file_uri, 2)
    timestamp = get_current_timestamp()
    await service.vikingdb_manager.upsert(
        {
            "id": record_id,
            "uri": file_uri,
            "context_type": "resource",
            "level": 2,
            "abstract": "unchanged summary",
            "created_at": timestamp,
            "updated_at": timestamp,
            "vector": [0.1] * service.vikingdb_manager.vector_dim,
            "search_tags": ["status=old"],
        },
        ctx=ctx,
    )
    original = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
    set_service(service)
    token = mcp_endpoint._mcp_ctx.set(ctx)
    try:

        async def call_tool(arguments):
            handler = mcp_endpoint.mcp._mcp_server.request_handlers[CallToolRequest]
            response = await handler(
                CallToolRequest(params=CallToolRequestParams(name="set_tags", arguments=arguments))
            )
            return response.root.model_dump(by_alias=True, exclude_none=True)

        async def update(**kwargs):
            response = await call_tool(kwargs)
            assert response.get("isError") is not True, response
            return response["structuredContent"]

        result = await update(uri="viking://~/resources/mcp-tags/note.md", tags=["Status=New"])
        assert result["uri"] == file_uri
        assert result["tags"] == ["status=new"]
        assert result["tags_updated"] is True
        assert "queue_status" not in result

        await update(uri=file_uri, tags=["team=search"], mode="append")
        await update(uri=file_uri, tags=[], mode="replace")  # HTTP: empty replace is a no-op.
        stored = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
        assert set(stored["search_tags"]) == {"status=new", "team=search"}
        assert {k: v for k, v in stored.items() if k != "search_tags"} == {
            k: v for k, v in original.items() if k != "search_tags"
        }

        await update(uri=root, tags=["status=reviewed"], recursive=True)
        stored = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
        assert stored["search_tags"] == ["status=reviewed"]
        await update(uri=file_uri, mode="clear")
        stored = (await service.vikingdb_manager.get_strict([record_id], ctx=ctx))[0]
        assert stored["search_tags"] == []
        assert await service.viking_fs.read_file(file_uri, ctx=ctx) == "unchanged body"

        for uri, code in [
            (f"{root}/missing.md", "NOT_FOUND"),
            ("viking://user/someone_else/resources/private.md", "PERMISSION_DENIED"),
        ]:
            error = await call_tool({"uri": uri, "mode": "clear"})
            assert error["isError"] is True
            assert error["structuredContent"]["error"]["code"] == code
    finally:
        mcp_endpoint._mcp_ctx.reset(token)
