# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A waiting embedding writer must recheck the source after acquiring its lock."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.storage.abstract_overview import semantic_body_digest
from openviking.storage.collection_schemas import TextEmbeddingHandler
from openviking.storage.ttl_registry import TTLRegistry
from tests.storage.test_transfer_merge_binding import binding_fs as binding_fs
from tests.storage.test_transfer_merge_binding import root_ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", ["original", "updated", None])
async def test_embedding_rechecks_changed_or_deleted_source_after_lock(
    binding_fs, monkeypatch, revision
):
    fs, ctx = binding_fs, root_ctx()
    owner = "viking://user/default/memories/events/2026/09/30"
    sidecar = owner + "/.abstract.md"
    await fs.write_file(owner + "/body.md", "body", ctx=ctx)
    await fs.write_file(sidecar, "original", ctx=ctx)
    read = AsyncMock(wraps=fs.read_file)
    monkeypatch.setattr(fs, "read_file", read)
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    handler = object.__new__(TextEmbeddingHandler)
    write = AsyncMock(return_value="vector-id")
    lease = await fs._async_agfs.pathlock_acquire_exact(
        TTLRegistry.vector_lock_path(ctx.account_id, owner)
    )
    consumer = asyncio.create_task(
        handler._write_ttl_vector_if_current(sidecar, semantic_body_digest("original"), ctx, write)
    )
    try:
        await asyncio.sleep(0.1)
        assert not consumer.done()
        read.assert_not_awaited()
        write.assert_not_awaited()
        if revision is None:
            await fs._async_agfs.rm(fs._uri_to_path(owner, ctx=ctx), recursive=True)
        elif revision == "updated":
            await fs.write_file(sidecar, revision, ctx=ctx)
    finally:
        await fs._async_agfs.pathlock_release(lease)
        try:
            result = await asyncio.wait_for(consumer, timeout=5)
        finally:
            if not consumer.done():
                consumer.cancel()
                await asyncio.gather(consumer, return_exceptions=True)
    assert result == ("vector-id" if revision == "original" else None)
    assert write.await_count == int(revision == "original")
    if revision is None:
        assert not await fs.exists(owner, ctx=ctx, include_expired=True)
