# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking_cli.session.user_id import UserIdentifier


def _mock_transaction_layer(monkeypatch):
    mock_handle = MagicMock()
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aenter__",
        AsyncMock(return_value=mock_handle),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.lock_context.LockContext.__aexit__",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager",
        lambda: MagicMock(),
    )


class _FakeVikingFS:
    def __init__(self, tree, file_contents):
        self._tree = {self._norm(k): v for k, v in tree.items()}
        self._file_contents = {self._norm(k): v for k, v in file_contents.items()}
        self.writes = []
        self.ls_calls: list[str] = []
        self.read_calls: list[str] = []

    def _norm(self, path):
        if "://" not in path:
            return path
        scheme, rest = path.split("://", 1)
        rest = re.sub(r"/{2,}", "/", rest)
        return f"{scheme}://{rest}"

    async def ls(self, uri, ctx=None):
        self.ls_calls.append(self._norm(uri))
        return self._tree.get(self._norm(uri), [])

    async def stat(self, uri, ctx=None):
        content = self._file_contents.get(self._norm(uri), "")
        return {"size": len(content)}

    async def read_file(self, path, ctx=None):
        normed = self._norm(path)
        self.read_calls.append(normed)
        return self._file_contents.get(normed, "")

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

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    def _enforce_size_limits(self, overview, abstract):
        return overview, abstract

    async def _sync_topdown_recursive(
        self,
        root_uri,
        target_uri,
        ctx=None,
        file_change_status=None,
        lifecycle_lock_handle_id="",
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


@pytest.mark.asyncio
async def test_incremental_missing_summary_triggers_overview_regen(monkeypatch):
    _mock_transaction_layer(monkeypatch)

    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"
    tree = {
        root_uri: [{"name": "a.txt", "isDir": False}],
        target_uri: [{"name": "a.txt", "isDir": False}],
    }

    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "hello",
            f"{target_uri}/a.txt": "hello",
            f"{target_uri}/.overview.md": "FILES:\n",
            f"{target_uri}/.abstract.md": "old-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    executor1 = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
    )
    monkeypatch.setattr(executor1, "_add_vectorize_task", AsyncMock())
    await executor1.run(root_uri)

    assert "- a.txt:" in fake_fs._file_contents[f"{root_uri}/.overview.md"]
    assert "- a.txt:" in fake_fs._file_contents[f"{target_uri}/.overview.md"]
    first_run_calls = len(processor.summarized_files)

    executor2 = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
    )
    monkeypatch.setattr(executor2, "_add_vectorize_task", AsyncMock())
    await executor2.run(root_uri)

    assert len(processor.summarized_files) == first_run_calls


@pytest.mark.asyncio
async def test_direct_incremental_update_uses_changes_without_temp_sync(monkeypatch):
    _mock_transaction_layer(monkeypatch)

    root_uri = "viking://resources/root"
    tree = {
        root_uri: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
        ],
    }

    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "new content",
            f"{root_uri}/b.txt": "unchanged",
            f"{root_uri}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
            f"{root_uri}/.abstract.md": "old-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=root_uri,
        changes={"modified": [f"{root_uri}/a.txt"]},
    )
    monkeypatch.setattr(executor, "_add_vectorize_task", AsyncMock())

    await executor.run(root_uri)

    assert processor.summarized_files == [f"{root_uri}/a.txt"]
    assert processor.sync_calls == []
    overview = fake_fs._file_contents[f"{root_uri}/.overview.md"]
    assert "- a.txt: summary" in overview
    assert "- b.txt: old-b" in overview


@pytest.mark.asyncio
async def test_direct_incremental_update_skips_unchanged_sibling_subtrees(monkeypatch):
    """With complete directory metadata, writing to dir_a/ should not
    dispatch the unchanged sibling dir_b/ subtree — reducing FS IO and
    preventing unnecessary embedding calls.

    Scenario: viking://resources/project_x/ has two child directories:
      - dir_a/ (with a changed file deep inside)
      - dir_b/ (fully unchanged, with its own nested files)

    All directories have complete .overview.md and .abstract.md — the
    typical production state after initial processing. The fix should
    prevent the sibling subtree from being traversed at all.
    """
    _mock_transaction_layer(monkeypatch)

    root_uri = "viking://resources/project_x"
    changed_file = f"{root_uri}/dir_a/sub_dir/changed.md"
    tree = {
        root_uri: [
            {"name": "dir_a", "isDir": True},
            {"name": "dir_b", "isDir": True},
        ],
        f"{root_uri}/dir_a": [
            {"name": "sub_dir", "isDir": True},
        ],
        f"{root_uri}/dir_a/sub_dir": [
            {"name": "changed.md", "isDir": False},
            {"name": "other.md", "isDir": False},
        ],
        f"{root_uri}/dir_b": [
            {"name": "nested", "isDir": True},
        ],
        f"{root_uri}/dir_b/nested": [
            {"name": "f1.md", "isDir": False},
            {"name": "f2.md", "isDir": False},
        ],
    }
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            changed_file: "new content",
            f"{root_uri}/dir_a/sub_dir/other.md": "unchanged",
            f"{root_uri}/dir_a/.overview.md": "FILES:\n",
            f"{root_uri}/dir_a/.abstract.md": "dir-a-abstract",
            f"{root_uri}/dir_a/sub_dir/.overview.md": (
                "FILES:\n- changed.md: old-c\n- other.md: old-o"
            ),
            f"{root_uri}/dir_a/sub_dir/.abstract.md": "sub-dir-abstract",
            f"{root_uri}/dir_b/.overview.md": "FILES:\n",
            f"{root_uri}/dir_b/.abstract.md": "dir-b-abstract",
            f"{root_uri}/dir_b/nested/.overview.md": ("FILES:\n- f1.md: old-f1\n- f2.md: old-f2"),
            f"{root_uri}/dir_b/nested/.abstract.md": "nested-abstract",
            f"{root_uri}/.overview.md": "FILES:\n",
            f"{root_uri}/.abstract.md": "root-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=root_uri,
        changes={"modified": [changed_file]},
    )
    monkeypatch.setattr(executor, "_add_vectorize_task", AsyncMock())

    await executor.run(root_uri)

    # Changed file MUST be summarized (ancestor chain is processed)
    summarized = set(processor.summarized_files)
    assert changed_file in summarized, f"Changed file {changed_file} must be summarized"
    assert len(summarized) == 1, f"Must only summarize {changed_file=} got {summarized=}"

    # Sibling subtree MUST NOT be ls'd at all
    sibling_deep_ls = [c for c in fake_fs.ls_calls if "dir_b/nested" in c]
    assert not sibling_deep_ls, f"Sibling children should not be ls'd, got: {sibling_deep_ls}"

    # Sibling subtree MUST NOT trigger any vectorize/embedding tasks
    vectorize_tasks = [call.args[0] for call in executor._add_vectorize_task.call_args_list]
    sibling_vectorize = [
        t
        for t in vectorize_tasks
        if "dir_b" in (getattr(t, "uri", None) or "")
        or "dir_b" in (getattr(t, "file_path", None) or "")
    ]
    assert not sibling_vectorize, (
        f"Sibling subtree should not be vectorized, got: "
        f"{[(t.task_type, getattr(t, 'uri', ''), getattr(t, 'file_path', '')) for t in sibling_vectorize]}"
    )

    # Sibling subtree files MUST NOT be read
    sibling_file_reads = [
        c
        for c in fake_fs.read_calls
        if "dir_b/" in c and not c.endswith(".abstract.md") and not c.endswith(".overview.md")
    ]
    assert not sibling_file_reads, f"Sibling files should not be read, got: {sibling_file_reads}"


if __name__ == "__main__":
    pytest.main([__file__])
