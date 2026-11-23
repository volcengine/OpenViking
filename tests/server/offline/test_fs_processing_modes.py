# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Processing controls keep files and vectors consistent without semantic jobs."""

from unittest.mock import AsyncMock

import pytest

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.queuefs import get_queue_manager
from openviking.utils.content_hash import content_md5
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


async def seed_referenced_memory(service, ctx, resource, memory):
    await service.viking_fs.write_file(resource, "resource body", ctx=ctx)
    await service.viking_fs.write_file(
        memory,
        MemoryFileUtils.write(
            MemoryFile(
                uri=memory,
                content=f"# Resource card\n\nReference: {resource}",
                extra_fields={
                    "category": "reference",
                    "name": "resource",
                    "resource_refs": [{"resource_uri": resource}],
                },
            )
        ),
        ctx=ctx,
    )
    await MemoryUpdater.refresh_file_embedding(
        viking_fs=service.viking_fs,
        vikingdb=service.vikingdb_manager,
        uri=memory,
        memory_type="entities",
        ctx=ctx,
        strict=True,
    )
    await service.resources.wait_processed()


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
    record["active_count"] = 42
    await service.vikingdb_manager.upsert(record, ctx=ctx)
    enqueue = forbid_semantics(monkeypatch)
    manager = get_queue_manager()
    embedding_queue = manager.get_queue(manager.EMBEDDING)
    original_enqueue = embedding_queue.enqueue

    async def enqueue_after_counter_update(msg):
        if msg.context_data.get("uri") == uri:
            latest = (await service.vikingdb_manager.get_strict([record["id"]], ctx=ctx))[0]
            latest["active_count"] = 99
            await service.vikingdb_manager.upsert(latest, ctx=ctx)
        return await original_enqueue(msg)

    monkeypatch.setattr(embedding_queue, "enqueue", enqueue_after_counter_update)
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
    assert updated["active_count"] == 99
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


async def test_webdav_vector_fingerprint_allows_reverting_content(client, service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/webdav-fingerprint/a.md"
    await service.fs.write(uri, "old body", ctx, wait=True, processing_mode="vectors_only")
    before = await service.vikingdb_manager.get_l2_diff_records_by_uris([uri], ctx=ctx)
    assert before[uri]["md5"] == content_md5(b"old body")
    enqueue = forbid_semantics(monkeypatch)
    response = await client.put(
        "/webdav/resources/webdav-fingerprint/a.md",
        content="new body",
        headers={"X-OpenViking-Processing-Mode": "vectors_only"},
    )
    assert response.status_code == 204, response.text
    await service.resources.wait_processed()
    updated = await service.vikingdb_manager.get_l2_diff_records_by_uris([uri], ctx=ctx)
    assert updated[uri]["md5"] == content_md5(b"new body")
    rewritten = await service.fs.write(
        uri, "old body", ctx, wait=True, processing_mode="vectors_only"
    )
    assert rewritten["vector_status"] == "complete"
    assert rewritten["queue_status"]["Embedding"]["processed"] == 1
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("wait", [True, False])
async def test_resource_delete_vectors_only_refreshes_referenced_memory_index(
    client, service, monkeypatch, wait
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    resource = "viking://resources/memory-ref/a.md"
    memory = f"viking://user/{ctx.user.user_id}/memories/entities/resource-ref.md"
    await seed_referenced_memory(service, ctx, resource, memory)
    before = await service.vikingdb_manager.fetch_by_uri(memory, ctx=ctx)
    assert resource in before["abstract"]
    enqueue = forbid_semantics(monkeypatch)
    extraction = AsyncMock(side_effect=AssertionError("resource cleanup must not extract memory"))
    monkeypatch.setattr(service._resource_memory_link_service, "on_resource_deleted", extraction)
    response = await client.delete(
        "/api/v1/fs", params={"uri": resource, "wait": wait, "processing_mode": "vectors_only"}
    )
    deleted = result(response)
    assert deleted["memory_cleanup"]["memory_commit"]["status"] == "skipped"
    if not wait:
        assert deleted["vector_status"] == "queued"
        task = await get_task_tracker().wait(
            deleted["task_id"], account_id=ctx.account_id, user_id=ctx.user.user_id, timeout=15
        )
        assert task.status == "completed"
    else:
        assert deleted["vector_status"] == "complete"
    # Waiting on this deletion suffices; no global queue drain is needed.
    after = await service.vikingdb_manager.fetch_by_uri(memory, ctx=ctx)
    assert resource not in after["abstract"]
    assert resource not in await service.viking_fs.read_file(memory, ctx=ctx)
    extraction.assert_not_awaited()
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("failure_stage", ["enqueue", "worker"])
async def test_resource_delete_reports_memory_embedding_failure(
    client, service, monkeypatch, failure_stage
):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    resource = "viking://resources/memory-failure/a.md"
    memory = f"viking://user/{ctx.user.user_id}/memories/entities/failure.md"
    await seed_referenced_memory(service, ctx, resource, memory)
    manager = get_queue_manager()
    if failure_stage == "enqueue":
        queue = manager.get_queue(manager.EMBEDDING)
        original_enqueue = queue.enqueue

        async def fail_memory_enqueue(msg):
            if msg.context_data.get("uri") == memory:
                raise RuntimeError("memory embedding queue unavailable")
            return await original_enqueue(msg)

        monkeypatch.setattr(queue, "enqueue", fail_memory_enqueue)
    else:
        original_upsert = service.vikingdb_manager.upsert

        async def fail_memory_upsert(data, **kwargs):
            if data.get("uri") == memory:
                raise RuntimeError("memory embedding write failed")
            return await original_upsert(data, **kwargs)

        monkeypatch.setattr(service.vikingdb_manager, "upsert", fail_memory_upsert)
    response = await client.delete(
        "/api/v1/fs", params={"uri": resource, "wait": True, "processing_mode": "vectors_only"}
    )
    assert response.status_code == 500, response.text
    assert response.json()["status"] == "error"
    assert not await service.viking_fs.exists(resource, ctx=ctx)


async def test_webdav_vectors_use_final_sanitized_content(client, service, monkeypatch):
    from types import SimpleNamespace

    ctx = RequestContext(user=service.user, role=Role.ROOT)
    await service.fs.mkdir("viking://resources/privacy-bytes", ctx)
    uri = "viking://resources/privacy-bytes/a.md"
    secret = "private-token-123456"
    stored_text = "credential=REDACTED"
    sanitizer = SimpleNamespace(
        sanitize_for_write=AsyncMock(
            side_effect=lambda content, **kwargs: content.replace(secret, "REDACTED")
        )
    )
    monkeypatch.setattr(service.viking_fs, "_privacy_sanitizer", sanitizer)
    manager = get_queue_manager()
    queue = manager.get_queue(manager.EMBEDDING)
    original_enqueue = queue.enqueue
    messages = []

    async def capture_embedding(msg):
        if msg.context_data.get("uri") == uri:
            messages.append(msg)
        return await original_enqueue(msg)

    monkeypatch.setattr(queue, "enqueue", capture_embedding)
    monkeypatch.setattr(
        service.vikingdb_manager, "account_uses_content_field", AsyncMock(return_value=True)
    )
    original_upsert = service.vikingdb_manager.upsert
    writes = []

    async def capture_upsert(data, **kwargs):
        if data.get("uri") == uri:
            writes.append(dict(data))
        return await original_upsert(data, **kwargs)

    monkeypatch.setattr(service.vikingdb_manager, "upsert", capture_upsert)
    response = await client.put(
        "/webdav/resources/privacy-bytes/a.md",
        content=f"credential={secret}",
        headers={"X-OpenViking-Processing-Mode": "vectors_only"},
    )
    assert response.status_code == 201, response.text
    await service.resources.wait_processed()
    stored = await service.viking_fs.read_file_bytes(uri, ctx=ctx)
    assert stored == stored_text.encode("utf-8")
    assert len(messages) == 1
    assert secret not in str(messages[0].message)
    assert messages[0].context_data["md5"] == content_md5(stored)
    assert len(writes) == 1
    assert writes[0]["content"] == stored_text
    assert writes[0]["md5"] == content_md5(stored)


async def test_webdav_vectors_only_clear_removes_old_detail_index(client, service, monkeypatch):
    ctx = RequestContext(user=service.user, role=Role.ROOT)
    uri = "viking://resources/webdav-empty/a.md"
    await service.fs.write(
        uri, "old searchable body", ctx, wait=True, processing_mode="vectors_only"
    )
    assert len(await records(service, uri, ctx)) == 1
    enqueue = forbid_semantics(monkeypatch)
    response = await client.put(
        "/webdav/resources/webdav-empty/a.md",
        content=b"",
        headers={"X-OpenViking-Processing-Mode": "vectors_only"},
    )
    assert response.status_code == 204, response.text
    await service.resources.wait_processed()
    assert await service.viking_fs.read_file_bytes(uri, ctx=ctx) == b""
    assert await records(service, uri, ctx) == []
    enqueue.assert_not_awaited()
