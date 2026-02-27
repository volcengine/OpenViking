# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []

    async def ls(self, uri, ctx=None):
        return self._tree.get(uri, [])

    async def write_file(self, path, content, ctx=None):
        self.writes.append((path, content))


class _FakeProcessor:
    def __init__(self):
        self.vectorized_dirs = []
        self.vectorized_files = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        return "overview"

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    async def _vectorize_directory_simple(self, uri, context_type, abstract, overview, ctx=None):
        self.vectorized_dirs.append(uri)

    async def _vectorize_single_file(
        self, parent_uri, context_type, file_path, summary_dict, ctx=None
    ):
        self.vectorized_files.append(file_path)


@pytest.mark.asyncio
async def test_semantic_dag_stats_collects_nodes(monkeypatch):
    root_uri = "viking://resources/root"
    tree = {
        root_uri: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
            {"name": "child", "isDir": True},
        ],
        f"{root_uri}/child": [
            {"name": "c.txt", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
    )
    await executor.run(root_uri)

    stats = executor.get_stats()
    assert isinstance(stats, DagStats)
    assert stats.total_nodes == 5  # 2 dirs + 3 files
    assert stats.pending_nodes == 0
    assert stats.done_nodes == 5
    assert stats.in_progress_nodes == 0
    assert processor.vectorized_dirs == [f"{root_uri}/child", root_uri]
    assert sorted(processor.vectorized_files) == sorted(
        [f"{root_uri}/a.txt", f"{root_uri}/b.txt", f"{root_uri}/child/c.txt"]
    )


if __name__ == "__main__":
    pytest.main([__file__])
