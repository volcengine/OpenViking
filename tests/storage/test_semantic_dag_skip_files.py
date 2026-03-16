# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
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
        self.summarized_files = []
        self.vectorized_files = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.summarized_files.append(file_path)
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        return "overview"

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    async def _vectorize_directory_simple(self, uri, context_type, abstract, overview, ctx=None):
        pass

    async def _vectorize_single_file(
        self, parent_uri, context_type, file_path, summary_dict, ctx=None
    ):
        self.vectorized_files.append(file_path)


@pytest.mark.asyncio
async def test_messages_jsonl_excluded_from_summary(monkeypatch):
    """messages.jsonl should be skipped by _list_dir and never summarized."""
    root_uri = "viking://sessions/test-session"
    tree = {
        root_uri: [
            {"name": "messages.jsonl", "isDir": False},
            {"name": "notes.txt", "isDir": False},
            {"name": "document.pdf", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="session",
        max_concurrent_llm=2,
        ctx=ctx,
    )
    await executor.run(root_uri)

    summarized_names = [p.split("/")[-1] for p in processor.summarized_files]
    assert "messages.jsonl" not in summarized_names
    assert "notes.txt" in summarized_names
    assert "document.pdf" in summarized_names


@pytest.mark.asyncio
async def test_messages_jsonl_excluded_in_subdirectory(monkeypatch):
    """messages.jsonl in a subdirectory should also be skipped."""
    root_uri = "viking://sessions/test-session"
    tree = {
        root_uri: [
            {"name": "subdir", "isDir": True},
        ],
        f"{root_uri}/subdir": [
            {"name": "messages.jsonl", "isDir": False},
            {"name": "data.csv", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="session",
        max_concurrent_llm=2,
        ctx=ctx,
    )
    await executor.run(root_uri)

    summarized_names = [p.split("/")[-1] for p in processor.summarized_files]
    assert "messages.jsonl" not in summarized_names
    assert "data.csv" in summarized_names


if __name__ == "__main__":
    pytest.main([__file__])
