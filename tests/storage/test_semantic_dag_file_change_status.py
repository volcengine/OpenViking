# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0


import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree, file_contents=None):
        self._tree = tree
        self._file_contents = file_contents or {}
        self.read_count = {}
        self.writes = []

    async def ls(self, uri, ctx=None, show_all_hidden=False):
        return self._tree.get(uri, [])

    async def read_file(self, path, ctx=None):
        self.read_count[path] = self.read_count.get(path, 0) + 1
        return self._file_contents.get(path, "")

    async def write_file(self, path, content, ctx=None):
        self.writes.append((path, content))

    async def exists(self, uri, ctx=None):
        return uri in self._tree or uri in self._file_contents

    async def rm(self, uri, recursive=False, ctx=None):
        pass

    async def mv(self, src, dst, ctx=None):
        pass

    async def mkdir(self, uri, exist_ok=False, ctx=None):
        pass

    def _get_vector_store(self):
        return None


class _FakeProcessor:
    def __init__(self):
        pass

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        return {"name": file_path.split("/")[-1], "summary": "summary"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        return "overview"

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    async def _vectorize_single_file(
        self, parent_uri, context_type, file_path, summary_dict, ctx=None, semantic_msg_id=None
    ):
        pass

    async def _vectorize_directory(
        self, uri, context_type, abstract, overview, ctx=None, semantic_msg_id=None
    ):
        pass


@pytest.mark.asyncio
async def test_file_change_status_initialized(monkeypatch):
    """Test that _file_change_status is initialized as an empty dict."""
    fake_fs = _FakeVikingFS({})
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
    )
    assert hasattr(executor, '_file_change_status')
    assert executor._file_change_status == {}


@pytest.mark.asyncio
async def test_file_change_status_recorded_in_non_incremental_mode(monkeypatch):
    """Test that file change status is recorded as True in non-incremental mode."""
    root_uri = "viking://resources/root"

    tree = {
        root_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "file2.txt", "isDir": False},
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

    assert f"{root_uri}/file1.txt" in executor._file_change_status
    assert f"{root_uri}/file2.txt" in executor._file_change_status

    assert executor._file_change_status[f"{root_uri}/file1.txt"]
    assert executor._file_change_status[f"{root_uri}/file2.txt"]


@pytest.mark.asyncio
async def test_file_change_status_recorded_in_incremental_mode(monkeypatch):
    """Test that file change status is recorded during incremental update."""
    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    tree = {
        root_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "file2.txt", "isDir": False},
        ],
        target_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "file2.txt", "isDir": False},
        ],
    }

    file_contents = {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2_new",
        f"{target_uri}/file2.txt": "content2_old",
    }

    fake_fs = _FakeVikingFS(tree, file_contents)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
    )

    await executor.run(root_uri)

    assert f"{root_uri}/file1.txt" in executor._file_change_status
    assert f"{root_uri}/file2.txt" in executor._file_change_status

    assert not executor._file_change_status[f"{root_uri}/file1.txt"]
    assert executor._file_change_status[f"{root_uri}/file2.txt"]


@pytest.mark.asyncio
async def test_compute_diff_uses_precomputed_status(monkeypatch):
    """Test that _compute_diff uses pre-computed file change status."""
    processor = SemanticProcessor()

    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    root_tree = {
        root_uri: (
            [],
            [f"{root_uri}/file1.txt", f"{root_uri}/file2.txt"]
        )
    }

    target_tree = {
        target_uri: (
            [],
            [f"{target_uri}/file1.txt", f"{target_uri}/file2.txt"]
        )
    }

    file_change_status = {
        f"{root_uri}/file1.txt": False,
        f"{root_uri}/file2.txt": True,
    }

    fake_fs = _FakeVikingFS({}, {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2_new",
        f"{target_uri}/file2.txt": "content2_old",
    })
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs)

    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    diff = await processor._compute_diff(
        root_tree, target_tree,
        root_uri, target_uri,
        ctx=ctx,
        file_change_status=file_change_status
    )

    assert f"{root_uri}/file1.txt" not in diff.updated_files
    assert f"{root_uri}/file2.txt" in diff.updated_files

    assert f"{root_uri}/file1.txt" not in fake_fs.read_count
    assert f"{root_uri}/file2.txt" not in fake_fs.read_count


@pytest.mark.asyncio
async def test_compute_diff_without_precomputed_status(monkeypatch):
    """Test that _compute_diff reads files when no pre-computed status is available."""
    processor = SemanticProcessor()

    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    root_tree = {
        root_uri: (
            [],
            [f"{root_uri}/file1.txt", f"{root_uri}/file2.txt"]
        )
    }

    target_tree = {
        target_uri: (
            [],
            [f"{target_uri}/file1.txt", f"{target_uri}/file2.txt"]
        )
    }

    fake_fs = _FakeVikingFS({}, {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2_new",
        f"{target_uri}/file2.txt": "content2_old",
    })
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs)

    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    diff = await processor._compute_diff(
        root_tree, target_tree,
        root_uri, target_uri,
        ctx=ctx,
        file_change_status=None
    )

    assert f"{root_uri}/file1.txt" not in diff.updated_files
    assert f"{root_uri}/file2.txt" in diff.updated_files

    assert f"{root_uri}/file1.txt" in fake_fs.read_count
    assert f"{root_uri}/file2.txt" in fake_fs.read_count


@pytest.mark.asyncio
async def test_optimization_reduces_file_reads(monkeypatch):
    """Test that the optimization reduces file read operations."""
    processor = SemanticProcessor()

    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    root_tree = {
        root_uri: (
            [],
            [f"{root_uri}/file1.txt", f"{root_uri}/file2.txt", f"{root_uri}/file3.txt"]
        )
    }

    target_tree = {
        target_uri: (
            [],
            [f"{target_uri}/file1.txt", f"{target_uri}/file2.txt", f"{target_uri}/file3.txt"]
        )
    }

    file_change_status = {
        f"{root_uri}/file1.txt": False,
        f"{root_uri}/file2.txt": True,
        f"{root_uri}/file3.txt": False,
    }

    fake_fs = _FakeVikingFS({}, {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2_new",
        f"{target_uri}/file2.txt": "content2_old",
        f"{root_uri}/file3.txt": "content3",
        f"{target_uri}/file3.txt": "content3",
    })
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs)

    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    await processor._compute_diff(
        root_tree, target_tree,
        root_uri, target_uri,
        ctx=ctx,
        file_change_status=file_change_status
    )

    total_reads_with_optimization = sum(fake_fs.read_count.values())

    fake_fs_no_opt = _FakeVikingFS({}, {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2_new",
        f"{target_uri}/file2.txt": "content2_old",
        f"{root_uri}/file3.txt": "content3",
        f"{target_uri}/file3.txt": "content3",
    })
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs_no_opt)

    await processor._compute_diff(
        root_tree, target_tree,
        root_uri, target_uri,
        ctx=ctx,
        file_change_status=None
    )

    total_reads_without_optimization = sum(fake_fs_no_opt.read_count.values())

    assert total_reads_with_optimization < total_reads_without_optimization
    assert total_reads_with_optimization == 0
    assert total_reads_without_optimization == 6


@pytest.mark.asyncio
async def test_file_change_status_with_nested_directories(monkeypatch):
    """Test file change status recording with nested directories."""
    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    tree = {
        root_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "subdir", "isDir": True},
        ],
        f"{root_uri}/subdir": [
            {"name": "file2.txt", "isDir": False},
        ],
        target_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "subdir", "isDir": True},
        ],
        f"{target_uri}/subdir": [
            {"name": "file2.txt", "isDir": False},
        ],
    }

    file_contents = {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/subdir/file2.txt": "content2",
        f"{target_uri}/subdir/file2.txt": "content2",
    }

    fake_fs = _FakeVikingFS(tree, file_contents)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
    )

    await executor.run(root_uri)

    assert f"{root_uri}/file1.txt" in executor._file_change_status
    assert f"{root_uri}/subdir/file2.txt" in executor._file_change_status

    assert not executor._file_change_status[f"{root_uri}/file1.txt"]
    assert not executor._file_change_status[f"{root_uri}/subdir/file2.txt"]


@pytest.mark.asyncio
async def test_file_change_status_with_new_file(monkeypatch):
    """Test that new files are marked as changed."""
    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    tree = {
        root_uri: [
            {"name": "file1.txt", "isDir": False},
            {"name": "new_file.txt", "isDir": False},
        ],
        target_uri: [
            {"name": "file1.txt", "isDir": False},
        ],
    }

    file_contents = {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/new_file.txt": "new_content",
    }

    fake_fs = _FakeVikingFS(tree, file_contents)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
    )

    await executor.run(root_uri)

    assert f"{root_uri}/file1.txt" in executor._file_change_status
    assert f"{root_uri}/new_file.txt" in executor._file_change_status

    assert not executor._file_change_status[f"{root_uri}/file1.txt"]
    assert executor._file_change_status[f"{root_uri}/new_file.txt"]


@pytest.mark.asyncio
async def test_compute_diff_partial_precomputed_status(monkeypatch):
    """Test _compute_diff with partial pre-computed status."""
    processor = SemanticProcessor()

    root_uri = "viking://resources/root"
    target_uri = "viking://resources/target"

    root_tree = {
        root_uri: (
            [],
            [f"{root_uri}/file1.txt", f"{root_uri}/file2.txt", f"{root_uri}/file3.txt"]
        )
    }

    target_tree = {
        target_uri: (
            [],
            [f"{target_uri}/file1.txt", f"{target_uri}/file2.txt", f"{target_uri}/file3.txt"]
        )
    }

    file_change_status = {
        f"{root_uri}/file1.txt": False,
    }

    fake_fs = _FakeVikingFS({}, {
        f"{root_uri}/file1.txt": "content1",
        f"{target_uri}/file1.txt": "content1",
        f"{root_uri}/file2.txt": "content2",
        f"{target_uri}/file2.txt": "content2",
        f"{root_uri}/file3.txt": "content3_changed",
        f"{target_uri}/file3.txt": "content3",
    })
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fake_fs)

    ctx = RequestContext(user=UserIdentifier("acc1", "user1", "agent1"), role=Role.USER)

    diff = await processor._compute_diff(
        root_tree, target_tree,
        root_uri, target_uri,
        ctx=ctx,
        file_change_status=file_change_status
    )

    assert f"{root_uri}/file1.txt" not in diff.updated_files
    assert f"{root_uri}/file2.txt" not in diff.updated_files
    assert f"{root_uri}/file3.txt" in diff.updated_files

    assert f"{root_uri}/file1.txt" not in fake_fs.read_count
    assert f"{root_uri}/file2.txt" in fake_fs.read_count
    assert f"{root_uri}/file3.txt" in fake_fs.read_count


if __name__ == "__main__":
    pytest.main([__file__])
