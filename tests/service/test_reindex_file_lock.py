# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exact-file reindex must protect real files during legacy chunk cleanup."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.server.identity import RequestContext, Role
from openviking.service import reindex_executor as reindex_module
from openviking.service.reindex_executor import ReindexExecutor
from openviking_cli.exceptions import NotFoundError
from openviking_cli.session.user_id import UserIdentifier


async def test_reindex_single_file_keeps_concurrent_chunk_named_file(monkeypatch):
    uri = "viking://user/bob/memories/events/a.md"
    stale, created = uri + "#chunk_0000", uri + "#chunk_0001"
    sources = {uri: "memory body"}
    stored = {
        "base": {"id": "base", "uri": uri, "level": 2, "abstract": "summary"},
        "stale": {"id": "stale", "uri": stale, "level": 2},
        "created": {"id": "created", "uri": created, "level": 2},
    }
    owned = set()

    async def acquire_file(path):
        assert path == "/local/test/" + uri.removeprefix("viking://")
        owned.add("file")
        return {"lease_id": "file"}

    async def acquire_chunks(paths, *, owner_lease_ref):
        assert owner_lease_ref == {"lease_id": "file"}
        assert set(paths) == {
            "/local/test/" + item.removeprefix("viking://") for item in (stale, created)
        }
        owned.add("chunks")
        return {"lease_id": "chunks"}

    async def release(lease):
        owned.remove(lease["lease_id"])

    async def stat(target, **kwargs):
        if target not in sources:
            raise NotFoundError(target)
        return {"isDir": False}

    async def enqueue(msg):
        assert msg.context_data["uri"] == uri
        # A sibling writer finishes after the first existence check.
        sources[created] = "new real file"
        stored["created"]["abstract"] = "new vector"
        return True

    async def delete(ids, **kwargs):
        assert owned == {"file", "chunks"}
        for record_id in ids:
            del stored[record_id]
        return len(ids)

    agfs = SimpleNamespace(
        pathlock_acquire_exact=acquire_file,
        pathlock_acquire_tree=AsyncMock(side_effect=AssertionError("file target used tree lock")),
        pathlock_acquire_exact_batch=acquire_chunks,
        pathlock_as_borrowed=AsyncMock(return_value={"lease_ref": "file"}),
        pathlock_release=release,
    )
    fs = SimpleNamespace(
        _async_agfs=agfs,
        _uri_to_path=lambda value, **kw: "/local/test/" + value.removeprefix("viking://"),
        stat=stat,
        exists=AsyncMock(side_effect=lambda value, **kw: value in sources),
        read_file=AsyncMock(side_effect=lambda value, **kw: sources[value]),
    )
    db = SimpleNamespace(
        has_queue_manager=True,
        get_reindex_inventory_under_uri=AsyncMock(return_value=dict(stored)),
        enqueue_embedding_msg=enqueue,
        strict_delete=delete,
    )
    service = SimpleNamespace(viking_fs=fs, vikingdb_manager=db)
    monkeypatch.setattr(reindex_module, "get_service", lambda: service)
    monkeypatch.setattr(reindex_module, "get_viking_fs", lambda: fs)
    executor = ReindexExecutor()
    monkeypatch.setattr(executor, "_effective_file_vectorization_concurrency", lambda: 1)
    ctx = RequestContext(user=UserIdentifier("test", "bob"), role=Role.USER)

    result = await executor._run(uri=uri, object_type="memory", mode="vectors_only", ctx=ctx)

    assert result["status"] == "completed"
    assert result["rebuilt_records"] == result["deleted_records"] == 1
    assert set(stored) == {"base", "created"}
    assert stored["created"]["abstract"] == "new vector"
    assert sources[created] == "new real file"
    assert not owned
