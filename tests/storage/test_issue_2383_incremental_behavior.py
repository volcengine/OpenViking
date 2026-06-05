# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import DagStats, SemanticDagExecutor
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
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
    def __init__(self, tree, contents):
        self._tree = tree
        self._contents = contents
        self.writes = []

    async def ls(self, uri, ctx=None, show_all_hidden=False):
        return list(self._tree.get(uri, []))

    async def write_file(self, path, content, ctx=None):
        self.writes.append((path, content))
        self._contents[path] = content

    async def stat(self, uri, ctx=None):
        if uri not in self._contents:
            raise FileNotFoundError(uri)
        content = self._contents[uri]
        if isinstance(content, bytes):
            size = len(content)
        else:
            size = len(str(content))
        return {"size": size}

    async def read_file(self, uri, ctx=None):
        if uri not in self._contents:
            raise FileNotFoundError(uri)
        return self._contents[uri]

    async def exists(self, uri, ctx=None):
        return uri in self._tree or uri in self._contents

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    def __init__(self):
        self.summarized_files = []
        self.overview_inputs = []
        self.vectorized_files = []
        self.vectorized_dirs = []

    def _parse_overview_md(self, overview_content):
        results = {}
        for line in overview_content.splitlines():
            line = line.strip()
            if not line.startswith("- ") or ":" not in line:
                continue
            name, summary = line[2:].split(":", 1)
            results[name.strip()] = summary.strip()
        return results

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.summarized_files.append(file_path)
        return {"name": file_path.split("/")[-1], "summary": "fresh summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        self.overview_inputs.append((dir_uri, list(file_summaries), list(children_abstracts)))
        lines = ["FILES:"]
        for item in file_summaries:
            lines.append(f"- {item['name']}: {item['summary']}")
        return "\n".join(lines)

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    def _enforce_size_limits(self, overview, abstract):
        return overview, abstract

    async def _vectorize_directory(
        self, uri, context_type, abstract, overview, ctx=None, semantic_msg_id=None
    ):
        self.vectorized_dirs.append(uri)

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        semantic_msg_id=None,
        use_summary=False,
    ):
        self.vectorized_files.append(file_path)

    async def _sync_topdown_recursive(
        self,
        root_uri,
        target_uri,
        ctx=None,
        file_change_status=None,
        lock=None,
    ):
        return SimpleNamespace(
            added_files=[],
            deleted_files=[],
            updated_files=[],
            added_dirs=[],
            deleted_dirs=[],
        )


class _DummyEmbeddingTracker:
    async def register(self, **_kwargs):
        return None


class _CapturedSemanticQueue:
    def __init__(self):
        self.messages = []

    async def enqueue(self, msg):
        self.messages.append(msg)
        return "queued"


class _FakeQueueManager:
    SEMANTIC = "semantic"

    def __init__(self, queue):
        self._queue = queue

    def get_queue(self, name, allow_create=False):
        assert name == self.SEMANTIC
        return self._queue


def _patch_dag_deps(monkeypatch, fake_fs):
    _mock_transaction_layer(monkeypatch)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_viking_fs",
        lambda: fake_fs,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyEmbeddingTracker(),
    )


def _ctx():
    return RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)


@pytest.mark.asyncio
async def test_incremental_unchanged_file_does_not_read_summary_or_vectorize(monkeypatch):
    temp_root = "viking://resources/_tmp/repeat-add"
    target_root = "viking://resources/repeat-add"
    temp_file = f"{temp_root}/alpha.md"
    target_file = f"{target_root}/alpha.md"

    fake_fs = _FakeVikingFS(
        tree={
            temp_root: [{"name": "alpha.md", "isDir": False}],
            target_root: [{"name": "alpha.md", "isDir": False}],
        },
        contents={
            temp_file: "# Alpha\n\nsame content\n",
            target_file: "# Alpha\n\nsame content\n",
            f"{target_root}/.overview.md": "This old prose is intentionally unparsable.",
            f"{target_root}/.abstract.md": "old abstract",
        },
    )
    _patch_dag_deps(monkeypatch, fake_fs)

    processor = _FakeProcessor()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=_ctx(),
        incremental_update=True,
        target_uri=target_root,
    )

    await executor.run(temp_root)
    await asyncio.sleep(0)

    assert processor.summarized_files == []
    assert processor.overview_inputs == []
    assert processor.vectorized_files == []
    assert processor.vectorized_dirs == []
    assert executor.has_effective_changes is False


@pytest.mark.asyncio
async def test_incremental_changed_file_summarizes_and_vectorizes(monkeypatch):
    temp_root = "viking://resources/_tmp/repeat-add"
    target_root = "viking://resources/repeat-add"
    temp_file = f"{temp_root}/alpha.md"
    target_file = f"{target_root}/alpha.md"

    fake_fs = _FakeVikingFS(
        tree={
            temp_root: [{"name": "alpha.md", "isDir": False}],
            target_root: [{"name": "alpha.md", "isDir": False}],
        },
        contents={
            temp_file: "# Alpha\n\nchanged content\n",
            target_file: "# Alpha\n\nold content\n",
            f"{target_root}/.overview.md": "FILES:\n- alpha.md: old summary",
            f"{target_root}/.abstract.md": "old abstract",
        },
    )
    _patch_dag_deps(monkeypatch, fake_fs)

    processor = _FakeProcessor()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=_ctx(),
        incremental_update=True,
        target_uri=target_root,
    )

    await executor.run(temp_root)
    await asyncio.sleep(0)

    assert processor.summarized_files == [temp_file]
    assert processor.vectorized_files == [temp_file]
    assert processor.vectorized_dirs == [temp_root]
    assert executor.has_effective_changes is True


@pytest.mark.asyncio
async def test_incremental_directory_change_reuses_unchanged_file_summary_at_directory_level(
    monkeypatch,
):
    root_uri = "viking://resources/root"
    changed_file = f"{root_uri}/a.txt"
    unchanged_file = f"{root_uri}/b.txt"

    fake_fs = _FakeVikingFS(
        tree={
            root_uri: [
                {"name": "a.txt", "isDir": False},
                {"name": "b.txt", "isDir": False},
            ],
        },
        contents={
            changed_file: "new content",
            unchanged_file: "unchanged",
            f"{root_uri}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
            f"{root_uri}/.abstract.md": "old abstract",
        },
    )
    _patch_dag_deps(monkeypatch, fake_fs)

    processor = _FakeProcessor()
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=_ctx(),
        incremental_update=True,
        target_uri=root_uri,
        changes={"modified": [changed_file]},
    )

    await executor.run(root_uri)
    await asyncio.sleep(0)

    assert processor.summarized_files == [changed_file]
    assert len(processor.overview_inputs) == 1
    _dir_uri, file_summaries, _child_abstracts = processor.overview_inputs[0]
    assert file_summaries == [
        {"name": "a.txt", "summary": "fresh summary"},
        {"name": "b.txt", "summary": "old-b"},
    ]
    assert processor.vectorized_files == [changed_file]
    assert processor.vectorized_dirs == [root_uri]


async def _run_semantic_processor_with_fake_executor(monkeypatch, has_effective_changes):
    parent_queue = _CapturedSemanticQueue()
    fake_qm = _FakeQueueManager(parent_queue)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: fake_qm,
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.get_viking_fs",
        lambda: SimpleNamespace(exists=AsyncMock(return_value=True)),
    )

    class _FakeSemanticScope:
        def __init__(self):
            self.lock = MagicMock()

        async def close(self):
            return None

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=_FakeSemanticScope()),
    )

    effective_changes = has_effective_changes

    class _FakeExecutor:
        stale = False
        has_effective_changes = effective_changes

        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def run(self, uri):
            return None

        def get_stats(self):
            return DagStats()

    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticDagExecutor",
        _FakeExecutor,
    )

    processor = SemanticProcessor(max_concurrent_llm=1)
    msg = SemanticMsg(
        uri="viking://resources/_tmp/repeat-add",
        context_type="resource",
        account_id="acc1",
        user_id="user1",
        agent_id="agent1",
        role=Role.USER.value,
        target_uri="viking://resources/codeask/wiki/repeat-add",
        target_preexisting=True,
    )

    await processor.on_dequeue(msg.to_dict())
    return parent_queue.messages


@pytest.mark.asyncio
async def test_incremental_resource_skips_parent_refresh_without_effective_changes(monkeypatch):
    messages = await _run_semantic_processor_with_fake_executor(
        monkeypatch,
        has_effective_changes=False,
    )

    assert messages == []


@pytest.mark.asyncio
async def test_incremental_resource_enqueues_parent_refresh_with_effective_changes(monkeypatch):
    messages = await _run_semantic_processor_with_fake_executor(
        monkeypatch,
        has_effective_changes=True,
    )

    assert len(messages) == 1
    parent_msg = messages[0]
    assert isinstance(parent_msg, SemanticMsg)
    assert parent_msg.uri == "viking://resources/codeask/wiki"
    assert parent_msg.recursive is False
    assert parent_msg.changes == {
        "modified": ["viking://resources/codeask/wiki/repeat-add"]
    }
