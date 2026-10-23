from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.resource_ingest import (
    AddResourceRequest,
    ingest_resource_request,
)
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_temp_upload_uses_active_server_config(monkeypatch):
    resolved = SimpleNamespace(
        local_path="/tmp/rules.md",
        original_filename="rules.md",
        cleanup=AsyncMock(),
    )
    store = SimpleNamespace(
        resolve_shared_reference=AsyncMock(return_value=None),
        resolve_for_consume=AsyncMock(return_value=resolved),
    )
    build_store = Mock(return_value=store)
    server_config = SimpleNamespace(temp_upload=SimpleNamespace())
    resources = SimpleNamespace(
        add_resource=AsyncMock(return_value={"root_uri": "viking://user/alice/resources/rules"})
    )
    monkeypatch.setattr(
        "openviking.server.resource_ingest.TempUploadStore.build",
        build_store,
    )
    monkeypatch.setattr(
        "openviking.server.dependencies.get_server_config",
        lambda: server_config,
    )

    await ingest_resource_request(
        AddResourceRequest(temp_file_id="upload_rules", source_name="rules.md"),
        RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER),
        resources=resources,
        config=SimpleNamespace(),
        wait=True,
        internal_task=True,
    )

    build_store.assert_called_once_with(server_config)
    store.resolve_for_consume.assert_awaited_once()
    assert resources.add_resource.await_args.kwargs["path"] == "/tmp/rules.md"
    resolved.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_shared_ingest_forwards_complete_request_with_rule_overrides():
    resources = SimpleNamespace(
        add_resource=AsyncMock(
            return_value={"root_uri": "viking://user/alice/resources/team-rules"}
        )
    )
    request = AddResourceRequest(
        path="space:home",
        add_type="feishu",
        to="viking://resources/team-rules",
        args={"feishu_access_token": "token"},
        strict=True,
        wait=False,
        internal_task=False,
        processing_mode="vectors_only",
        tags=["rules"],
        tag_mode="append",
    )
    ctx = RequestContext(
        user=UserIdentifier("acme", "alice"),
        role=Role.USER,
    )

    result = await ingest_resource_request(
        request,
        ctx,
        resources=resources,
        config=None,
        wait=True,
        internal_task=True,
    )

    assert result["root_uri"].endswith("/team-rules")
    kwargs = resources.add_resource.await_args.kwargs
    assert kwargs["path"] == "space:home"
    assert kwargs["add_type"] == "feishu"
    assert kwargs["to"] == "viking://resources/team-rules"
    assert kwargs["args"] == {"feishu_access_token": "token"}
    assert kwargs["strict"] is True
    assert kwargs["processing_mode"] == "vectors_only"
    assert kwargs["tags"] == ["rules"]
    assert kwargs["tag_mode"] == "append"
    assert kwargs["wait"] is True
    assert kwargs["internal_task"] is True
