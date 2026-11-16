# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource copy and move boundary tests."""

from types import SimpleNamespace

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
async def test_copy_resource_delegates_to_fs_cp(monkeypatch):
    calls = []

    async def fake_cp(request, _ctx=None):
        calls.append((request, _ctx))
        return SimpleNamespace(result={"from": request.from_uri, "to": request.to_uri})

    monkeypatch.setattr(resources, "copy_filesystem_entry", fake_cp)
    ctx = _ctx()
    response = await resources.copy_resource(
        CpRequest(
            from_uri="viking://resources/source/",
            to_uri="viking://resources/archive/copy/",
            recursive=True,
        ),
        _ctx=ctx,
    )

    assert len(calls) == 1
    request = calls[0][0]
    assert (request.from_uri, request.to_uri, request.recursive) == (
        "viking://resources/source",
        "viking://resources/archive/copy",
        True,
    )
    assert calls[0][1] is ctx
    assert response.result["to"] == "viking://resources/archive/copy"


@pytest.mark.asyncio
async def test_move_resource_delegates_to_fs_mv(monkeypatch):
    calls = []

    async def fake_mv(request, _ctx=None):
        calls.append((request, _ctx))
        return SimpleNamespace(result={"from": request.from_uri, "to": request.to_uri})

    monkeypatch.setattr(resources, "move_filesystem_entry", fake_mv)
    ctx = _ctx()
    await resources.move_resource(
        MvRequest(
            from_uri="viking://resources/old-name.md",
            to_uri="viking://resources/new-name.md",
        ),
        _ctx=ctx,
    )

    assert len(calls) == 1
    request = calls[0][0]
    assert (request.from_uri, request.to_uri) == (
        "viking://resources/old-name.md",
        "viking://resources/new-name.md",
    )
    assert calls[0][1] is ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["copy", "move"])
@pytest.mark.parametrize(
    ("from_uri", "to_uri"),
    [
        ("viking://resources", "viking://resources/copy"),
        ("viking://user/alice/resources/private", "viking://resources/copy"),
        ("viking://resources/source", "viking://resources"),
        ("viking://resources/source", "viking://agent/tools/copy"),
        ("viking://resources/source", "viking://resources-other/copy"),
        ("viking://resources/../agent/tools/source", "viking://resources/copy"),
        ("viking://resources/source", "viking://resources/%2e%2e/agent/tools/copy"),
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
async def test_resource_transfer_http_copy_and_move_real_directory(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    source = "viking://resources/transfer-source"
    copied = "viking://resources/transfer-copy"
    moved = "viking://resources/transfer-renamed"
    await service.fs.mkdir(source, ctx=ctx)

    copy_response = await client.post(
        "/api/v1/resources/cp",
        json={"from_uri": source, "to_uri": copied, "recursive": True},
    )
    assert copy_response.status_code == 200, copy_response.text
    assert (await service.fs.stat(source, ctx=ctx))["isDir"] is True
    assert (await service.fs.stat(copied, ctx=ctx))["isDir"] is True

    move_response = await client.post(
        "/api/v1/resources/mv",
        json={"from_uri": copied, "to_uri": moved},
    )
    assert move_response.status_code == 200, move_response.text
    with pytest.raises(NotFoundError):
        await service.fs.stat(copied, ctx=ctx)
    assert (await service.fs.stat(moved, ctx=ctx))["isDir"] is True
    assert (await service.fs.stat(source, ctx=ctx))["isDir"] is True


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
