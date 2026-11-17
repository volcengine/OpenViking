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
from openviking.storage.abstract_overview import freshness_metadata, render_abstract_overview
from openviking.storage.vector_ids import vector_record_id
from openviking_cli.exceptions import InvalidArgumentError, NotFoundError
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.vlm_config import VLMConfig


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
async def test_delete_resource_http_succeeds_when_parent_summary_is_locked(
    client, service, monkeypatch, wait
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    viking_fs = service.viking_fs
    parent_uri = "viking://resources/docs"
    uri = f"{parent_uri}/delete-me.txt"
    await viking_fs.write_file(uri, "content", ctx=ctx)
    metadata = {"freshness": freshness_metadata(1000, 32)}
    sidecars = []
    for name, level in [
        (".abstract.md", ContextLevel.ABSTRACT),
        (".overview.md", ContextLevel.OVERVIEW),
    ]:
        sidecar_uri = f"{parent_uri}/{name}"
        await viking_fs.write_file(
            sidecar_uri,
            render_abstract_overview(level, parent_uri, "summary", metadata),
            ctx=ctx,
        )
        sidecars.append(viking_fs._uri_to_path(sidecar_uri, ctx=ctx))

    lease = await viking_fs._async_agfs.pathlock_acquire_exact_batch(sidecars)
    if wait:
        queue = service._queue_manager.get_queue(service._queue_manager.SEMANTIC, allow_create=True)
        enqueue = queue.enqueue

        async def enqueue_after_unlock(msg, **kwargs):
            nonlocal lease
            # Release the competing writer once the fallback reaches enqueue,
            # so wait=True can observe the actual queued refresh completing.
            if lease is not None:
                await viking_fs._async_agfs.pathlock_release(lease)
                lease = None
            return await enqueue(msg, **kwargs)

        monkeypatch.setattr(queue, "enqueue", enqueue_after_unlock)
    try:
        response = await asyncio.wait_for(
            client.delete("/api/v1/resources", params={"uri": uri, "wait": wait}),
            timeout=10,
        )
    finally:
        if lease is not None:
            await viking_fs._async_agfs.pathlock_release(lease)

    assert response.status_code == 200, response.text
    assert response.json()["result"]["semantic_status"] == ("complete" if wait else "queued")
    assert not await viking_fs.exists(uri, ctx=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait,slow_refresh", [(False, False), (True, False), (False, True)])
async def test_concurrent_delete_distinct_siblings_removes_files_and_index(
    client, service, monkeypatch, wait, slow_refresh
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    viking_fs = service.viking_fs
    vector_store = viking_fs._get_vector_store()
    vector_dim = (await vector_store._get_backend_for_context(ctx)).vector_dim
    parent_uri = "viking://resources/concurrent-delete"
    uris = [f"{parent_uri}/document-{index:04d}.txt" for index in range(64)]
    priming_uris = [f"{parent_uri}/prime-{index}.txt" for index in range(8)] if slow_refresh else []
    records = []
    for uri in uris + priming_uris:
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
    assert len(await vector_store.get(ids, ctx=ctx)) == len(records)
    metadata = {"freshness": freshness_metadata(len(records), 32)}
    for name, level in [
        (".abstract.md", ContextLevel.ABSTRACT),
        (".overview.md", ContextLevel.OVERVIEW),
    ]:
        await viking_fs.write_file(
            f"{parent_uri}/{name}",
            render_abstract_overview(level, parent_uri, "summary", metadata),
            ctx=ctx,
        )

    model_started = asyncio.Event()
    resume_model = asyncio.Event()
    if slow_refresh:
        complete = VLMConfig.get_completion_async

        async def paused_completion(vlm, *args, **kwargs):
            model_started.set()
            await resume_model.wait()
            return await complete(vlm, *args, **kwargs)

        monkeypatch.setattr(VLMConfig, "get_completion_async", paused_completion)
    semaphore = asyncio.Semaphore(32)

    async def remove(uri):
        async with semaphore:
            return await client.delete("/api/v1/resources", params={"uri": uri, "wait": wait})

    try:
        # Eight setup deletes cross the normal freshness threshold for 72 files.
        # The remaining 64 targets are then deleted while model work is in flight.
        for uri in priming_uris:
            response = await client.delete("/api/v1/resources", params={"uri": uri, "wait": False})
            assert response.status_code == 200, response.text
        if slow_refresh:
            await asyncio.wait_for(model_started.wait(), timeout=10)
        responses = await asyncio.wait_for(
            asyncio.gather(*(remove(uri) for uri in uris)), timeout=60
        )
    finally:
        resume_model.set()
    await asyncio.wait_for(service._queue_manager.wait_complete(), timeout=60)
    remaining_files = [uri for uri in uris if await viking_fs.exists(uri, ctx=ctx)]
    remaining_records = await vector_store.get(ids, ctx=ctx)
    statuses = [response.status_code for response in responses]
    print(
        {
            "wait": wait,
            "slow_refresh": slow_refresh,
            "setup_deletes": len(priming_uris),
            "HTTP": {status: statuses.count(status) for status in set(statuses)},
            "remaining_files": len(remaining_files),
            "remaining_L2": len(remaining_records),
        }
    )
    assert remaining_files == []
    assert remaining_records == []
    assert all(response.status_code == 200 for response in responses), [
        response.json() for response in responses if response.status_code != 200
    ]


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
