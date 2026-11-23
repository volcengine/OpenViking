# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Request-boundary coverage for add_resource parse modes."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server import mcp_endpoint
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources as resources_router
from openviking.server.routers.resources import AddResourceRequest
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_resources import _wait_task_terminal


async def test_add_resource_route_forwards_no_split_mode(client, upload_temp_dir):
    source = upload_temp_dir / "guide.md"
    content = "# Setup\n\nInstall the package.\n\n# Upgrade\n\nRebuild the index.\n"
    source.write_text(content)
    payload = {"temp_file_id": source.name, "wait": True}

    rejected = await client.post("/api/v1/resources", json={**payload, "parse_mode": "no_split"})
    assert rejected.status_code == 400, rejected.text
    assert rejected.json()["error"]["code"] == "INVALID_ARGUMENT"

    imported = await client.post(
        "/api/v1/resources", json={**payload, "args": {"parse_mode": "no_split"}}
    )
    assert imported.status_code == 200, imported.text
    target = imported.json()["result"]["root_uri"]
    assert target == "viking://resources/guide.md"
    read = await client.get("/api/v1/content/read", params={"uri": target})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content.strip()


@pytest.mark.asyncio
@pytest.mark.usefixtures("public_resource_dns")
async def test_add_resource_route_forwards_connector_auth_config(monkeypatch: pytest.MonkeyPatch):
    add_resource = AsyncMock(
        return_value={"status": "success", "root_uri": "viking://resources/docs"}
    )
    service = SimpleNamespace(resources=SimpleNamespace(add_resource=add_resource))
    monkeypatch.setattr(resources_router, "get_service", lambda: service)
    ctx = RequestContext(
        user=UserIdentifier("test_account", "test_user"),
        role=Role.USER,
    )
    reference = {
        "openviking_credential_ref": {
            "account_id": "test_account",
            "resource_id": "ov-resource",
            "user_id": "test_user",
            "ov_user_id": "ov-user",
            "type": "oauth",
            "platform": "dingtalk",
            "binding_version": 3,
        }
    }

    # The managed credential channel is accepted despite extra="forbid" and is
    # forwarded verbatim to the service layer.
    await resources_router.add_resource(
        SimpleNamespace(headers={}),
        AddResourceRequest(
            path="https://alidocs.dingtalk.com/i/nodes/NODE1",
            add_type="dingtalk",
            to="viking://resources/docs",
            args={"binding_version": 3},
            connector_auth_config=reference,
        ),
        ctx,
    )

    assert add_resource.await_args.kwargs["connector_auth_config"] == reference


async def test_signed_temp_upload_forwards_token_bound_parse_mode(
    client, upload_temp_dir, monkeypatch
):
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "http://testserver")
    ctx = RequestContext(
        user=UserIdentifier.the_default_user(),
        role=Role(Role.USER),
    )
    context_token = mcp_endpoint._mcp_ctx.set(ctx)
    try:
        instructions = await mcp_endpoint.add_resource(
            path="/tmp/manual.md",
            parent="viking://~/resources/signed",
            args={"parse_mode": "no_split"},
        )
    finally:
        mcp_endpoint._mcp_ctx.reset(context_token)

    upload_url = next(
        line.strip()
        for line in instructions.splitlines()
        if line.strip().startswith("http://testserver/api/v1/resources/temp_upload?")
    )
    content = "# Start\n\nRead this first.\n\n# Finish\n\nKeep this in the same file.\n"
    response = await client.post(
        upload_url, files={"file": ("manual.md", content, "text/markdown")}
    )
    assert response.status_code == 200, response.text
    task = await _wait_task_terminal(client, response.json()["result"]["task_id"])
    assert task["status"] == "completed", task
    target = task["result"]["root_uri"]
    assert target == "viking://user/default/resources/signed/manual.md"
    read = await client.get("/api/v1/content/read", params={"uri": target})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content.strip()
