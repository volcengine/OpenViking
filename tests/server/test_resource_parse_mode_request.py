# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Request-boundary coverage for add_resource parse modes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources as resources_router
from openviking.server.routers.resources import AddResourceRequest
from openviking.server.upload_token_store import ConsumedUploadToken
from openviking_cli.session.user_id import UserIdentifier


def test_add_resource_request_exposes_parse_mode_only_inside_args():
    request = AddResourceRequest(
        path="https://example.com/demo.md",
        args={"parse_mode": "no_split"},
        preserve_structure=False,
    )

    assert "parse_mode" not in AddResourceRequest.model_json_schema()["properties"]
    assert request.args == {"parse_mode": "no_split"}


@pytest.mark.asyncio
async def test_add_resource_route_forwards_no_split_mode(monkeypatch: pytest.MonkeyPatch):
    add_resource = AsyncMock(
        return_value={"status": "success", "root_uri": "viking://resources/demo"}
    )
    service = SimpleNamespace(resources=SimpleNamespace(add_resource=add_resource))
    monkeypatch.setattr(resources_router, "get_service", lambda: service)
    ctx = RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )

    await resources_router.add_resource(
        SimpleNamespace(),
        AddResourceRequest(
            path="https://example.com/demo.md",
            args={"parse_mode": "no_split"},
        ),
        ctx,
    )

    assert add_resource.await_args.kwargs["args"] == {"parse_mode": "no_split"}


@pytest.mark.asyncio
async def test_signed_temp_upload_forwards_token_bound_parse_mode(
    monkeypatch: pytest.MonkeyPatch,
):
    signed = ConsumedUploadToken(
        account_id="test_account",
        user_id="test_user",
        to="",
        reason="",
        actor_peer_id="",
        parse_mode="no_split",
    )
    request = SimpleNamespace(
        state=SimpleNamespace(signed_upload=signed),
        app=SimpleNamespace(state=SimpleNamespace(config=object())),
    )
    store = SimpleNamespace(save_upload=AsyncMock(return_value="upload-id"))
    monkeypatch.setattr(resources_router.TempUploadStore, "build", lambda _config: store)
    ingest = AsyncMock(return_value={"root_uri": "viking://resources/upload"})
    monkeypatch.setattr(resources_router, "ingest_temp_upload", ingest)

    async def run_operation(*, operation, telemetry, fn):
        return SimpleNamespace(result=await fn(), telemetry=None)

    monkeypatch.setattr(resources_router, "run_operation", run_operation)

    await resources_router.temp_upload(
        request,
        object(),
        False,
        "local",
        RequestContext(
            user=UserIdentifier("test_account", "test_user"),
            role=Role.USER,
        ),
    )

    assert ingest.await_args.kwargs["parse_mode"] == "no_split"
