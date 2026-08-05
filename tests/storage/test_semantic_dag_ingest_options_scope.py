# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ingest-options (search tags) scoping across DAG vectorize tasks.

Directory L0/L1 records summarize sibling files, so tags carried by a
changes-scoped run (content write / incremental refresh) must reach only
the changed files' records — never the directory records, whose existing
tags survive via the embedding partial_update.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.utils.ingest_options import IngestOptions
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree, file_contents):
        self._tree = {self._norm(k): v for k, v in tree.items()}
        self._file_contents = {self._norm(k): v for k, v in file_contents.items()}
        self.writes = []

    def _norm(self, path):
        if "://" not in path:
            return path
        scheme, rest = path.split("://", 1)
        rest = re.sub(r"/{2,}", "/", rest)
        return f"{scheme}://{rest}"

    async def ls(self, uri, node_limit=None, ctx=None):
        return self._tree.get(self._norm(uri), [])

    async def stat(self, uri, ctx=None):
        content = self._file_contents.get(self._norm(uri), "")
        return {"size": len(content)}

    async def read_file(self, path, ctx=None):
        return self._file_contents.get(self._norm(path), "")

    async def write_file(self, path, content, ctx=None):
        norm_path = self._norm(path)
        self._file_contents[norm_path] = content
        self.writes.append((norm_path, content))

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    def __init__(self, viking_fs):
        self._fs = viking_fs
        self.summarized_files = []
        self.sync_calls = []

    def _parse_overview_md(self, overview_content):
        results = {}
        for line in overview_content.splitlines():
            m = re.match(r"^-\s*(?P<name>[^:]+):\s*(?P<summary>.*)$", line.strip())
            if not m:
                continue
            results[m.group("name").strip()] = m.group("summary").strip()
        return results

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.summarized_files.append(file_path)
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        lines = ["FILES:"]
        for item in file_summaries:
            name = item.get("name", "")
            summary = item.get("summary", "")
            lines.append(f"- {name}: {summary}")
        return "\n".join(lines)

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

    async def _sync_topdown_recursive(
        self, root_uri, target_uri, ctx=None, file_change_status=None, lock=None
    ):
        self.sync_calls.append((root_uri, target_uri))
        root_uri = self._fs._norm(root_uri)
        target_uri = self._fs._norm(target_uri)
        for path, content in list(self._fs._file_contents.items()):
            if path.startswith(root_uri + "/"):
                mapped = target_uri + path[len(root_uri) :]
                self._fs._file_contents[mapped] = content
        return MagicMock(
            added_files=[],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[],
        )


_ROOT = "viking://resources/root"
_TAGS = ["team=security"]


def _build_executor(monkeypatch, *, incremental_update, changes):
    fake_fs = _FakeVikingFS(
        tree={
            _ROOT: [
                {"name": "a.txt", "isDir": False},
                {"name": "b.txt", "isDir": False},
            ],
        },
        file_contents={
            f"{_ROOT}/a.txt": "new content",
            f"{_ROOT}/b.txt": "sibling content",
            f"{_ROOT}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
            f"{_ROOT}/.abstract.md": "old-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    executor = SemanticDagExecutor(
        processor=_FakeProcessor(fake_fs),
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=incremental_update,
        target_uri=_ROOT,
        changes=changes,
        ingest_options=IngestOptions(search_tags=list(_TAGS), search_tag_mode="replace"),
    )
    capture = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", capture)
    return executor, capture


def _tasks_by_type(capture):
    tasks = [call.args[0] for call in capture.call_args_list]
    return (
        [t for t in tasks if t.task_type == "file"],
        [t for t in tasks if t.task_type == "directory"],
    )


@pytest.mark.asyncio
async def test_changes_scoped_run_tags_only_the_changed_file(monkeypatch):
    executor, capture = _build_executor(
        monkeypatch,
        incremental_update=True,
        changes={"modified": [f"{_ROOT}/a.txt"]},
    )

    await executor.run(_ROOT)

    file_tasks, dir_tasks = _tasks_by_type(capture)
    assert [t.uri for t in file_tasks] == [f"{_ROOT}/a.txt"]
    assert file_tasks[0].ingest_options.search_tags == _TAGS
    assert dir_tasks, "directory refresh should still be vectorized"
    for task in dir_tasks:
        assert task.ingest_options.search_tags is None


@pytest.mark.asyncio
async def test_full_run_tags_files_and_directories(monkeypatch):
    executor, capture = _build_executor(
        monkeypatch,
        incremental_update=False,
        changes=None,
    )

    await executor.run(_ROOT)

    file_tasks, dir_tasks = _tasks_by_type(capture)
    assert {t.uri for t in file_tasks} == {f"{_ROOT}/a.txt", f"{_ROOT}/b.txt"}
    for task in file_tasks + dir_tasks:
        assert task.ingest_options.search_tags == _TAGS
