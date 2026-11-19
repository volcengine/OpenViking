# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource copy and move boundary tests."""

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources
from openviking.server.routers.filesystem import CpRequest, MvRequest
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "from_uri", "to_uri"),
    [
        ("copy", "viking://resources", "viking://resources/copy"),
        ("move", "viking://user/alice/resources/private", "viking://resources/copy"),
        ("move", "viking://resources/source", "viking://resources"),
        ("copy", "viking://resources/source", "viking://agent/tools/copy"),
        ("copy", "viking://resources/source", "viking://resources-other/copy"),
        ("move", "viking://resources/../agent/tools/source", "viking://resources/copy"),
        ("copy", "viking://resources/source", "viking://resources/%2e%2e/agent/tools/copy"),
    ],
)
async def test_resource_transfer_rejects_non_shared_source_or_target(
    operation,
    from_uri,
    to_uri,
    monkeypatch,
):
    async def unexpected_transfer(*args, **kwargs):
        raise AssertionError("rejected URI must not reach filesystem transfer")

    monkeypatch.setattr(resources, "copy_filesystem_entry", unexpected_transfer)
    monkeypatch.setattr(resources, "move_filesystem_entry", unexpected_transfer)
    handler = resources.copy_resource if operation == "copy" else resources.move_resource
    request = (
        CpRequest(from_uri=from_uri, to_uri=to_uri, recursive=True)
        if operation == "copy"
        else MvRequest(from_uri=from_uri, to_uri=to_uri)
    )

    with pytest.raises(InvalidArgumentError):
        await handler(request, _ctx=_ctx())


@pytest.mark.asyncio
async def test_resource_transfer_http_copy_and_move_preserves_file_contents(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    source = "viking://resources/transfer-source"
    copied = "viking://resources/transfer-copy"
    moved = "viking://resources/transfer-renamed"
    child = "nested/readme.md"
    content = "# Resource transfer\n\nKeep this file intact."
    await service.viking_fs.write_file(f"{source}/{child}", content, ctx=ctx)

    copy_response = await client.post(
        "/api/v1/resources/cp",
        json={"from_uri": f"{source}/", "to_uri": f"{copied}/", "recursive": True},
    )
    assert copy_response.status_code == 200, copy_response.text
    assert await service.fs.read(f"{source}/{child}", ctx=ctx) == content
    assert await service.fs.read(f"{copied}/{child}", ctx=ctx) == content

    move_response = await client.post(
        "/api/v1/resources/mv",
        json={"from_uri": copied, "to_uri": moved},
    )
    assert move_response.status_code == 200, move_response.text
    with pytest.raises(NotFoundError):
        await service.fs.stat(copied, ctx=ctx)
    assert await service.fs.read(f"{moved}/{child}", ctx=ctx) == content
    assert await service.fs.read(f"{source}/{child}", ctx=ctx) == content


@pytest.mark.asyncio
async def test_resource_transfer_http_rejects_private_target_before_service(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def unexpected_transfer(*args, **kwargs):
        raise AssertionError("private target must not reach filesystem transfer")

    monkeypatch.setattr(service.fs, "cp", unexpected_transfer)

    response = await client.post(
        "/api/v1/resources/cp",
        json={
            "from_uri": "viking://resources/source",
            "to_uri": "viking://user/alice/resources/private",
            "recursive": True,
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
