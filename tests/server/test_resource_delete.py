# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource deletion boundary tests."""

from types import SimpleNamespace

import httpx
import pytest

from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("acct", "alice"), role=Role.USER)


@pytest.mark.asyncio
async def test_delete_resource_delegates_to_fs_with_index_wait(monkeypatch):
    calls = []

    async def fake_rm(uri, recursive=False, wait=False, _ctx=None):
        calls.append((uri, recursive, wait, _ctx))
        return SimpleNamespace(result={"uri": uri, "semantic_status": "complete"})

    monkeypatch.setattr(resources, "remove_filesystem_entry", fake_rm)
    ctx = _ctx()

    response = await resources.delete_resource(
        uri="viking://resources/project/",
        recursive=True,
        wait=True,
        _ctx=ctx,
    )

    assert calls == [("viking://resources/project", True, True, ctx)]
    assert response.result["semantic_status"] == "complete"


@pytest.mark.asyncio
async def test_delete_resource_rejects_shared_root(monkeypatch):
    async def unexpected_rm(**kwargs):
        raise AssertionError("rejected URI must not reach filesystem deletion")

    monkeypatch.setattr(resources, "remove_filesystem_entry", unexpected_rm)

    with pytest.raises(InvalidArgumentError):
        await resources.delete_resource(uri="viking://resources/", _ctx=_ctx())


@pytest.mark.asyncio
async def test_delete_resource_http_forwards_recursive_and_wait(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    calls = []

    async def fake_rm(uri, ctx=None, recursive=False, wait=False):
        calls.append((uri, ctx, recursive, wait))
        return {"uri": uri, "semantic_status": "complete"}

    monkeypatch.setattr(service.fs, "rm", fake_rm)

    response = await client.delete(
        "/api/v1/resources",
        params={
            "uri": "viking://resources/docs/readme.md",
            "recursive": True,
            "wait": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "uri": "viking://resources/docs/readme.md",
        "semantic_status": "complete",
    }
    assert len(calls) == 1
    assert calls[0][0] == "viking://resources/docs/readme.md"
    assert calls[0][2:] == (True, True)


@pytest.mark.asyncio
async def test_delete_resource_http_removes_real_shared_directory(
    client: httpx.AsyncClient,
    service,
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/delete-me"
    await service.fs.mkdir(uri, ctx=ctx)

    response = await client.delete(
        "/api/v1/resources",
        params={"uri": f"{uri}/", "recursive": True, "wait": True},
    )

    assert response.status_code == 200, response.text
    assert response.json()["result"]["uri"] == uri
    with pytest.raises(NotFoundError):
        await service.fs.stat(uri, ctx=ctx)


@pytest.mark.asyncio
async def test_delete_resource_http_rejects_private_resource_before_service(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    async def unexpected_rm(*args, **kwargs):
        raise AssertionError("private resource must not reach filesystem deletion")

    monkeypatch.setattr(service.fs, "rm", unexpected_rm)

    response = await client.delete(
        "/api/v1/resources",
        params={"uri": "viking://user/alice/resources/private.md"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
