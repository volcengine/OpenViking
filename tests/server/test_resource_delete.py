# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared-resource deletion boundary tests."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from openviking.core.context import Context, ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import resources
from openviking.storage.abstract_overview import freshness_metadata, write_abstract_overview
from openviking.storage.vector_ids import vector_record_id
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
@pytest.mark.parametrize(
    "uri",
    [
        "viking://resources",
        "viking://resources/",
        "viking://user/alice/resources/private.md",
        "viking://agent/skills/demo",
        "viking://",
        "viking://resources-other/demo",
        "viking://resources/../agent/tools/demo",
        "viking://resources/%2e%2e/agent/tools/demo",
    ],
)
async def test_delete_resource_rejects_root_and_non_shared_subtree(uri, monkeypatch):
    async def unexpected_rm(**kwargs):
        raise AssertionError("rejected URI must not reach filesystem deletion")

    monkeypatch.setattr(resources, "remove_filesystem_entry", unexpected_rm)

    with pytest.raises(InvalidArgumentError):
        await resources.delete_resource(uri=uri, _ctx=_ctx())


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
@pytest.mark.parametrize("wait", [False, True])
async def test_concurrent_delete_distinct_siblings_removes_files_and_index(client, service, wait):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    viking_fs = service.viking_fs
    vector_store = viking_fs._get_vector_store()
    vector_dim = (await vector_store._get_backend_for_context(ctx)).vector_dim
    parent_uri = "viking://resources/concurrent-delete"
    uris = [f"{parent_uri}/document-{index:04d}.txt" for index in range(64)]
    records = []
    for uri in uris:
        await viking_fs.write_file(uri, "content", ctx=ctx)
        record = Context(
            uri=uri,
            parent_uri=parent_uri,
            is_leaf=True,
            level=ContextLevel.DETAIL,
            user=service.user,
            id=vector_record_id(ctx.account_id, uri),
        )
        record.vector = [0.1] * vector_dim
        records.append(record.to_dict())
    await vector_store.upsert_many(records, ctx=ctx)
    ids = [record["id"] for record in records]
    assert len(await vector_store.get(ids, ctx=ctx)) == 64
    await write_abstract_overview(
        viking_fs=viking_fs,
        dir_uri=parent_uri,
        overview="summary",
        abstract="summary",
        ctx=ctx,
        metadata={"freshness": freshness_metadata(64, 32)},
    )
    semaphore = asyncio.Semaphore(32)

    async def remove(uri):
        async with semaphore:
            return await client.delete("/api/v1/resources", params={"uri": uri, "wait": wait})

    responses = await asyncio.wait_for(asyncio.gather(*(remove(uri) for uri in uris)), timeout=60)
    await asyncio.wait_for(service._queue_manager.wait_complete(), timeout=60)
    assert all(response.status_code == 200 for response in responses), [
        response.json() for response in responses if response.status_code != 200
    ]
    assert not [uri for uri in uris if await viking_fs.exists(uri, ctx=ctx)]
    assert await vector_store.get(ids, ctx=ctx) == []


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
