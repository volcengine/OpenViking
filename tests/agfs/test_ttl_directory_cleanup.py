# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Delete every level within the lifecycle directory; keep external summaries."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.service.ttl_cleanup import TTLCleanupService
from openviking.storage.abstract_overview import render_abstract_overview
from openviking.storage.directory_ttl import read_directory_fields
from openviking.storage.errors import LockAcquisitionError, StorageException
from openviking.storage.ttl_registry import TTLRegistry
from openviking.storage.vector_ids import vector_record_id
from openviking_cli.exceptions import NotFoundError
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import indexed_fs as indexed_fs
from tests.storage.test_transfer_merge_binding import root_ctx
from tests.unit.service.test_ttl_cleanup import _cleanup_once
from tests.unit.storage.ttl_test_storage import read_record


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["event", "session"])
@pytest.mark.parametrize("fail_confirmation", [False, True])
async def test_cleanup_removes_files_all_vectors_and_metadata(
    indexed_fs, monkeypatch, kind, fail_confirmation
):
    fs, vectors = indexed_fs
    ctx = root_ctx()
    owner = (
        "viking://user/default/sessions/s1"
        if kind == "session"
        else "viking://user/default/memories/events/2026/09/28"
    )
    parent = owner.rsplit("/", 1)[0]
    bodies = [
        owner + "/messages.jsonl",
        owner + "/attachments/a.bin",
        owner + "/history/archive_001/body.txt",
    ]
    if kind == "session":
        await fs.write_file(owner + "/.meta.json", json.dumps({"session_id": "s1"}), ctx=ctx)
    for uri in bodies:
        await fs.write_file_bytes(uri, b"expired payload", ctx=ctx)
    summary_bytes = {}
    for folder in [parent, owner, owner + "/history/archive_001"]:
        for level, name in [(0, ".abstract.md"), (1, ".overview.md")]:
            uri = folder + "/" + name
            data = render_abstract_overview(level, folder, f"Summary L{level}").encode()
            summary_bytes[uri] = data
            await fs.write_file_bytes(uri, data, ctx=ctx)
            await vectors.upsert(
                {
                    "id": vector_record_id(ctx.account_id, folder, level),
                    "uri": folder,
                    "level": level,
                    "abstract": "Summary",
                    "vector": [0.1, 0.2, 0.3, 0.4],
                },
                ctx=ctx,
            )
    for uri in [*bodies, owner + "/orphan.txt"]:
        await vectors.upsert(
            {
                "id": vector_record_id(ctx.account_id, uri, 2),
                "uri": uri,
                "level": 2,
                "abstract": "body",
                "vector": [0.1, 0.2, 0.3, 0.4],
            },
            ctx=ctx,
        )
    backend = await vectors._get_backend_for_context(ctx)
    stored = await backend.strict_query(limit=100)
    assert len(stored) == 10
    assert all("expires_at" not in row for row in stored)
    assert "expires_at" not in {
        field["FieldName"] for field in backend._get_collection().get_meta_data()["Fields"]
    }
    await fs.write_file(
        owner + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
    )
    record = await read_record(fs, ctx.account_id, owner)
    for uri in [
        owner,
        *bodies,
        owner + "/.abstract.md",
        owner + "/history/archive_001/.overview.md",
    ]:
        with pytest.raises(NotFoundError):
            await fs.stat(uri, ctx=ctx)
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    monkeypatch.setattr(
        fs, "_collect_uris", AsyncMock(side_effect=AssertionError("unnecessary subtree scan"))
    )
    if fail_confirmation:
        with monkeypatch.context() as patch:
            patch.setattr(
                fs, "_confirm_fs_scope_cleared", AsyncMock(side_effect=OSError("confirm failed"))
            )
            with pytest.raises(StorageException, match="confirm failed"):
                await _cleanup_once(cleanup, record)
        # Deletion completed; only the final confirmation request failed.
        # With no body or vectors left, no expiry entry needs to be retained.
        assert not (await _cleanup_once(cleanup, record))["deleted"]
    else:
        assert (await _cleanup_once(cleanup, record))["deleted"]
    assert await read_record(fs, ctx.account_id, owner) is None
    assert not await fs.exists(owner, ctx=ctx)
    for uri, data in summary_bytes.items():
        if uri.startswith(owner + "/"):
            with pytest.raises(NotFoundError):
                await fs.read_file_bytes(uri, ctx=ctx)
        else:
            assert await fs.read_file_bytes(uri, ctx=ctx) == data
    remaining = await backend.strict_query(limit=100)
    assert {(r["uri"], r["level"]) for r in remaining} == {(parent, 0), (parent, 1)}


@pytest.mark.asyncio
async def test_busy_file_defers_cleanup_without_tree_lock(binding_fs, monkeypatch):
    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/memories/events/2026/09/28"
    body = root + "/a.txt"
    await fs.write_file(body, "busy", ctx=ctx)
    await fs.write_file(
        root + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
    )
    record = await read_record(fs, ctx.account_id, root)
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    lock = await fs._async_agfs.pathlock_acquire_exact(fs._uri_to_path(body, ctx=ctx))
    monkeypatch.setattr(
        fs._async_agfs, "pathlock_acquire_tree", AsyncMock(side_effect=AssertionError("tree lock"))
    )
    try:
        with pytest.raises(Exception) as failure:
            await asyncio.wait_for(_cleanup_once(cleanup, record), timeout=3)
        assert not isinstance(failure.value, (AssertionError, TimeoutError))
        assert (await read_directory_fields(fs, root, ctx=ctx))[
            "expires_at"
        ] == "2000-01-01T00:00:00Z"
        assert await read_record(fs, ctx.account_id, root) == record
    finally:
        await fs._async_agfs.pathlock_release(lock)
    assert (await _cleanup_once(cleanup, record))["deleted"]


@pytest.mark.asyncio
async def test_active_embedding_defers_cleanup_until_vector_write_finishes(binding_fs):
    fs, ctx = binding_fs, root_ctx()
    owner = "viking://user/default/memories/events/2026/09/28"
    await fs.write_file(owner + "/event.txt", "body", ctx=ctx)
    await fs.write_file(
        owner + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
    )
    record = await read_record(fs, ctx.account_id, owner)
    cleanup = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    guard = await fs._async_agfs.pathlock_acquire_exact(
        TTLRegistry.vector_lock_path(ctx.account_id, owner)
    )
    try:
        with pytest.raises(LockAcquisitionError):
            await _cleanup_once(cleanup, record)
        assert await read_record(fs, ctx.account_id, owner) == record
    finally:
        await fs._async_agfs.pathlock_release(guard)
    assert (await _cleanup_once(cleanup, record))["deleted"]


@pytest.mark.asyncio
async def test_worker_cleanup_and_late_summary_do_not_recreate_session(binding_fs, monkeypatch):
    import threading

    from openviking.service.task_store import PersistentTaskStore
    from openviking.service.task_tracker import TaskTracker, set_task_tracker
    from openviking.service.ttl_cleanup import _ttl_cleanup_message, _TTLCleanupProcessor
    from openviking.storage.queuefs.process_result import ProcessOutcome

    fs, ctx = binding_fs, root_ctx()
    root = "viking://user/default/sessions/worker"
    await fs.write_file(root + "/.meta.json", json.dumps({"session_id": "worker"}), ctx=ctx)
    await fs.write_file(root + "/messages.jsonl", "content", ctx=ctx)
    await fs.write_file(
        root + "/.meta.json", json.dumps({"expires_at": "2000-01-01T00:00:00Z"}), ctx=ctx
    )
    record = await read_record(fs, ctx.account_id, root)
    service = TTLCleanupService(service=SimpleNamespace(viking_fs=fs))
    processor = _TTLCleanupProcessor(service)
    set_task_tracker(TaskTracker(PersistentTaskStore(fs._async_agfs)))
    threads = []
    process = service._process

    async def tracked(message):
        threads.append(threading.get_ident())
        return await process(message)

    monkeypatch.setattr(service, "_process", tracked)
    try:
        result = await asyncio.to_thread(
            lambda: asyncio.run(processor.on_dequeue(_ttl_cleanup_message(record=record)))
        )
        assert result.outcome is ProcessOutcome.SUCCESS
        assert threads == [threads[0]] and threads[0] != threading.get_ident()
        for child in [
            ".abstract.md",
            "history/archive_001/.overview.md",
            "history/archive_001/.done",
        ]:
            with pytest.raises(NotFoundError):
                await fs.write_file(root + "/" + child, "late", ctx=ctx)
            assert not await fs.exists(root, ctx=ctx)
    finally:
        set_task_tracker(None)
