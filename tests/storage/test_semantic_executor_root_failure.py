# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Root dispatch failures must fail the semantic run instead of being acked."""

from unittest.mock import AsyncMock

import pytest

from openviking.pyagfs.exceptions import AGFSClientError, AGFSNotADirectoryError
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from openviking_cli.session.user_id import UserIdentifier
from tests.storage.test_semantic_executor_stats import (
    _FakeProcessor,
    _FakeVikingFS,
    _patch_semantic_config,
)


def _executor(**kwargs) -> SemanticTreeExecutor:
    return SemanticTreeExecutor(
        processor=_FakeProcessor(),
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_root_listing_failure_fails_the_run(monkeypatch):
    root_uri = "viking://resources/docs"
    fake_fs = _FakeVikingFS({})
    fake_fs.ls = AsyncMock(side_effect=AGFSClientError("backend I/O timeout"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fake_fs
    )
    _patch_semantic_config(monkeypatch)
    executor = _executor(generation_trigger="manual_refresh")

    with pytest.raises(AGFSClientError, match="backend I/O timeout"):
        await executor.run(root_uri)

    assert executor.get_stats().failures == []


@pytest.mark.asyncio
async def test_root_listing_failure_fails_content_write_run(monkeypatch):
    root_uri = "viking://resources"
    changed = f"{root_uri}/write-root.md"
    fake_fs = _FakeVikingFS({})
    fake_fs.ls = AsyncMock(side_effect=AGFSClientError("backend I/O timeout"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fake_fs
    )
    _patch_semantic_config(monkeypatch)
    executor = _executor(
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [changed]},
        generation_trigger="content_write",
        aggregate_directory=False,
    )

    with pytest.raises(AGFSClientError, match="backend I/O timeout"):
        await executor.run(root_uri)


@pytest.mark.asyncio
async def test_file_shaped_agfs_root_still_skips(monkeypatch):
    root_uri = "viking://resources/file-shaped.md"
    fake_fs = _FakeVikingFS({})
    fake_fs.ls = AsyncMock(side_effect=AGFSNotADirectoryError("not a directory"))
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fake_fs
    )
    _patch_semantic_config(monkeypatch)
    executor = _executor(generation_trigger="manual_refresh", skip_vectorization=True)

    # Missing or file-shaped roots keep their intentional skip (#4461 / #4474).
    await executor.run(root_uri)


@pytest.mark.asyncio
async def test_child_listing_failure_remains_tolerated(monkeypatch):
    root_uri = "viking://resources/root"
    child_uri = f"{root_uri}/child"
    tree = {
        root_uri: [
            {"name": "a.txt", "isDir": False},
            {"name": "child", "isDir": True},
        ],
    }
    fake_fs = _FakeVikingFS(tree)

    async def selective_ls(uri, node_limit=None, ctx=None):
        if uri == child_uri:
            raise AGFSClientError("backend I/O timeout")
        return tree.get(uri, [])

    fake_fs.ls = selective_ls
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fake_fs
    )
    _patch_semantic_config(monkeypatch)
    processor = _FakeProcessor()
    executor = SemanticTreeExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        skip_vectorization=True,
    )

    await executor.run(root_uri)

    # The parent still summarizes its own files and writes its sidecars even
    # though the child listing failed.
    assert processor.summarized_files == [f"{root_uri}/a.txt"]
    assert any(path == f"{root_uri}/.abstract.md" for path, _ in fake_fs.writes)
