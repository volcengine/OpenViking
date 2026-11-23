# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Direct indexing must use the real parent for single-file targets."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.utils import embedding_utils
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.parametrize("is_dir", [False, True])
async def test_index_resource_file_uses_actual_parent(monkeypatch, is_dir):
    uri = "viking://resources/docs/a.md"
    fs = SimpleNamespace(
        _get_vector_store=lambda: None,
        read_file_bytes=AsyncMock(return_value=b"snapshot body"),
        stat=AsyncMock(return_value={"isDir": is_dir}),
        exists=AsyncMock(return_value=False),
        ls=AsyncMock(side_effect=NotADirectoryError(uri))
        if not is_dir
        else AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    vectorize = AsyncMock(return_value=True)
    monkeypatch.setattr(embedding_utils, "vectorize_file", vectorize)
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    await embedding_utils.index_resource(uri, ctx)
    if is_dir:
        vectorize.assert_not_awaited()
        fs.ls.assert_awaited_once()
    else:
        vectorize.assert_awaited_once()
        assert vectorize.await_args.kwargs["file_path"] == uri
        assert vectorize.await_args.kwargs["parent_uri"] == "viking://resources/docs"
        fs.ls.assert_not_awaited()


async def test_index_file_embeds_and_hashes_the_same_bytes(monkeypatch):
    from openviking.utils.content_hash import content_md5

    uri = "viking://resources/docs/a.md"
    snapshot = "新正文".encode("utf-8")
    fs = SimpleNamespace(
        _get_vector_store=lambda: None,
        stat=AsyncMock(return_value={"isDir": False}),
        read_file_bytes=AsyncMock(return_value=snapshot),
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    vectorize = AsyncMock(return_value=True)
    monkeypatch.setattr(embedding_utils, "vectorize_file", vectorize)
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    await embedding_utils.index_resource(uri, ctx)
    assert vectorize.await_args.kwargs["file_content"] == snapshot
    assert vectorize.await_args.kwargs["file_md5"] == content_md5(snapshot)
    fs.read_file_bytes.assert_awaited_once_with(uri, ctx=ctx)


@pytest.mark.parametrize("vikingdb", [None, SimpleNamespace(has_queue_manager=False)])
async def test_strict_memory_refresh_requires_embedding_queue(vikingdb):
    from openviking.session.memory.memory_updater import MemoryUpdater

    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    with pytest.raises(RuntimeError, match="embedding queue"):
        await MemoryUpdater.refresh_file_embedding(
            viking_fs=object(),
            vikingdb=vikingdb,
            uri="viking://user/alice/memories/entities/a.md",
            memory_type="entities",
            ctx=ctx,
            strict=True,
        )
    assert (
        await MemoryUpdater.refresh_file_embedding(
            viking_fs=object(),
            vikingdb=vikingdb,
            uri="viking://user/alice/memories/entities/a.md",
            memory_type="entities",
            ctx=ctx,
        )
        is False
    )


async def test_index_file_does_not_report_rejected_embedding_success(monkeypatch):
    uri = "viking://resources/docs/a.md"
    fs = SimpleNamespace(
        _get_vector_store=lambda: None,
        stat=AsyncMock(return_value={"isDir": False}),
        read_file_bytes=AsyncMock(return_value=b"body"),
    )
    monkeypatch.setattr(embedding_utils, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(embedding_utils, "vectorize_file", AsyncMock(return_value=False))
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    with pytest.raises(RuntimeError, match="vectorization"):
        await embedding_utils.index_resource(uri, ctx)
