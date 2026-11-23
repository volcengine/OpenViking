# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing controls keep files and vectors consistent without semantic jobs."""

from unittest.mock import AsyncMock

import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.queuefs import get_queue_manager
from tests.server.offline.test_functional_api import result


@pytest.fixture(autouse=True)
def processing_identity(app, service):
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=service.user,
        role=Role.ROOT,
    )


async def records(service, uri, ctx):
    return await service.vikingdb_manager.filter(
        filter={"op": "must", "field": "uri", "conds": [uri]},
        limit=10,
        output_fields=["uri", "level"],
        ctx=ctx,
    )


def forbid_semantics(monkeypatch):
    manager = get_queue_manager()
    enqueue = AsyncMock(side_effect=AssertionError("vectors_only must not enqueue semantics"))
    monkeypatch.setattr(manager.get_queue(manager.SEMANTIC), "enqueue", enqueue)
    return enqueue


async def write_vectors(client, uri):
    return result(
        await client.post(
            "/api/v1/content/write",
            json={
                "uri": uri,
                "content": "# Test\n\nIndexed file body",
                "wait": True,
                "processing_mode": "vectors_only",
            },
        )
    )


async def test_vectors_only_copy_move_and_delete_keep_uri_vectors(client, service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    source = "viking://resources/source/a.md"
    target = "viking://resources/target/b.md"
    moved = "viking://resources/target/c.md"
    await service.fs.mkdir("viking://resources/target", ctx)
    await write_vectors(client, source)
    old_summary = await service.fs.abstract("viking://resources/target", ctx)
    enqueue = forbid_semantics(monkeypatch)
    copied = result(
        await client.post(
            "/api/v1/fs/cp",
            json={
                "from_uri": source,
                "to_uri": target,
                "processing_mode": "vectors_only",
            },
        )
    )
    assert copied["semantic_status"] == "skipped"
    assert len(await records(service, source, ctx)) == 1
    assert len(await records(service, target, ctx)) == 1
    moved_result = result(
        await client.post(
            "/api/v1/fs/mv",
            json={
                "from_uri": target,
                "to_uri": moved,
                "processing_mode": "vectors_only",
            },
        )
    )
    assert moved_result["semantic_status"] == "skipped"
    assert await records(service, target, ctx) == []
    assert len(await records(service, moved, ctx)) == 1
    deleted = result(
        await client.delete(
            "/api/v1/fs",
            params={
                "uri": moved,
                "wait": True,
                "processing_mode": "vectors_only",
            },
        )
    )
    assert deleted["vector_status"] == "complete"
    assert await records(service, moved, ctx) == []
    assert await service.fs.abstract("viking://resources/target", ctx) == old_summary
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("operation", ["cp", "mv"])
@pytest.mark.parametrize("directory", [False, True])
async def test_file_only_transfer_vectors_only_rebuilds_searchable_target(
    client, service, monkeypatch, operation, directory
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    source_file = "viking://system/memory/memory_templates/custom/fs-mode/source.md"
    await service.viking_fs.write_file(source_file, "# New source\n\nTransfer body", ctx=ctx)
    source = source_file.rsplit("/", 1)[0] if directory else source_file
    target = "viking://resources/transferred" if directory else "viking://resources/transferred.md"
    target_file = target + "/source.md" if directory else target
    enqueue = forbid_semantics(monkeypatch)
    body = {"from_uri": source, "to_uri": target, "processing_mode": "vectors_only"}
    if operation == "cp":
        body["recursive"] = directory
    transfer = result(await client.post(f"/api/v1/fs/{operation}", json=body))
    assert transfer["semantic_status"] == "skipped"
    assert transfer["vector_status"] == "queued"
    terminal = await get_task_tracker().wait(
        transfer["task_id"], timeout=15, account_id=ctx.account_id, user_id=ctx.user.user_id
    )
    assert terminal.status == "completed", terminal
    found = await records(service, target_file, ctx)
    assert len(found) == 1, found
    assert found[0]["level"] == 2
    assert (
        await service.viking_fs.read_file(target_file, ctx=ctx) == "# New source\n\nTransfer body"
    )
    assert await service.viking_fs.exists(source_file, ctx=ctx) is (operation == "cp")
    enqueue.assert_not_awaited()


async def test_webdav_vectors_only_indexes_file_with_real_parent(client, service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await service.fs.mkdir("viking://resources/webdav-mode", ctx)
    uri = "viking://resources/webdav-mode/a.md"
    await write_vectors(client, uri)
    record = await service.vikingdb_manager.fetch_by_uri(uri, ctx=ctx)
    record = (await service.vikingdb_manager.get_strict([record["id"]], ctx=ctx))[0]
    record["abstract"] = "Existing file summary"
    record["created_at"] = "2020-01-01T00:00:00Z"
    await service.vikingdb_manager.upsert(record, ctx=ctx)
    enqueue = forbid_semantics(monkeypatch)
    response = await client.put(
        "/webdav/resources/webdav-mode/a.md",
        content="# WebDAV\n\nFile content",
        headers={"X-OpenViking-Processing-Mode": "vectors_only"},
    )
    assert response.status_code == 204, response.text
    await service.resources.wait_processed()
    found = await records(service, "viking://resources/webdav-mode/a.md", ctx)
    assert len(found) == 1, found
    assert found[0]["uri"] == "viking://resources/webdav-mode/a.md"
    assert found[0]["level"] == 2
    updated = await service.vikingdb_manager.fetch_by_uri(uri, ctx=ctx)
    assert updated["abstract"] == "Existing file summary"
    assert updated["created_at"].startswith("2020-01-01")
    enqueue.assert_not_awaited()


async def test_single_file_restore_vectors_only_skips_semantics(client, service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/restore-mode/a.md"
    await write_vectors(client, uri)
    snapshot = await service.fs.commit(message="original", paths=[uri], ctx=ctx)
    await service.viking_fs.write_file(uri, "different body", ctx=ctx)
    enqueue = forbid_semantics(monkeypatch)
    restored = result(
        await client.post(
            "/api/v1/snapshot/restore",
            json={
                "path": uri,
                "restore_commit_id": snapshot["commit_oid"],
                "processing_mode": "vectors_only",
            },
        )
    )
    assert restored["uri"] == uri
    await service.resources.wait_processed()
    assert await service.viking_fs.read_file(uri, ctx=ctx) == "# Test\n\nIndexed file body"
    assert len(await records(service, uri, ctx)) == 1
    enqueue.assert_not_awaited()
