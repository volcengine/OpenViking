# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.queuefs.semantic_executor import SemanticTreeExecutor
from openviking.storage.viking_fs import VikingFS


@pytest.fixture
def wide_import(monkeypatch):
    fs = VikingFS(agfs=object())
    source = "viking://temp/import"
    target = "viking://resources/wixqa"
    names = [f"doc-{i:04d}" for i in range(1001)]
    source_children = set(names)
    target_children = set()

    async def ls(uri, *, node_limit=1000, **kwargs):
        names_at_uri = source_children if uri == source else target_children
        return [{"name": name, "isDir": True} for name in sorted(names_at_uri)[:node_limit]]

    async def exists(uri, **kwargs):
        return uri in (source, target)

    async def mv(old_uri, new_uri, **kwargs):
        name = old_uri.removeprefix(source + "/")
        assert new_uri == f"{target}/{name}"
        source_children.remove(name)
        target_children.add(name)

    monkeypatch.setattr(fs, "ls", ls)
    monkeypatch.setattr(fs, "exists", exists)
    monkeypatch.setattr(fs, "mv", mv)
    return fs, source, target, names, source_children, target_children


@pytest.mark.asyncio
async def test_sync_materializes_all_children_above_default_node_limit(wide_import):
    fs, source, target, names, source_children, target_children = wide_import

    diff = await fs.sync_tree(source, target)

    assert diff.added_dirs == [f"{target}/{name}" for name in names]
    assert not source_children
    assert target_children == set(names)


@pytest.mark.asyncio
async def test_semantic_executor_list_dir_enumerates_all_children(monkeypatch, wide_import):
    fs, source, _, names, _, _ = wide_import
    monkeypatch.setattr("openviking.storage.queuefs.semantic_executor.get_viking_fs", lambda: fs)
    executor = SemanticTreeExecutor(
        processor=None,
        context_type="resource",
        max_concurrent_llm=1,
        ctx=None,
    )

    directories, files = await executor._list_dir(source, from_hint="ingest")

    assert directories == [f"{source}/{name}" for name in names]
    assert files == []
