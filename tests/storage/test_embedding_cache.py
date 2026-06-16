# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the .embedding_cache.json sidecar (issue #2383)."""

import json
import re
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import embedding_cache as embedding_cache_module
from openviking.storage.queuefs.embedding_cache import (
    EMBEDDING_CACHE_FILENAME,
    compute_embedding_cache_key,
    load_embedding_cache,
    write_embedding_cache,
)
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
    """Minimal in-memory VikingFS supporting stat/read/write/mv/ls."""

    def __init__(self, tree: Dict[str, List[Dict[str, Any]]], file_contents: Dict[str, str]):
        self._tree = {self._norm(k): v for k, v in tree.items()}
        self._file_contents: Dict[str, str] = {
            self._norm(k): v for k, v in file_contents.items()
        }
        self._mod_times: Dict[str, str] = {}
        self.writes: List[tuple] = []

    @staticmethod
    def _norm(path: str) -> str:
        if "://" not in path:
            return path
        scheme, rest = path.split("://", 1)
        rest = re.sub(r"/{2,}", "/", rest)
        return f"{scheme}://{rest}"

    async def ls(self, uri, node_limit=None, ctx=None):
        return self._tree.get(self._norm(uri), [])

    async def stat(self, uri, ctx=None):
        norm = self._norm(uri)
        if norm not in self._file_contents:
            raise FileNotFoundError(uri)
        content = self._file_contents[norm]
        return {
            "size": len(content),
            "modTime": self._mod_times.get(norm, "2026-01-01T00:00:00Z"),
        }

    async def read_file(self, path, ctx=None):
        return self._file_contents.get(self._norm(path), "")

    async def write_file(self, path, content, ctx=None):
        norm = self._norm(path)
        self._file_contents[norm] = content
        self.writes.append((norm, content))

    async def mv(self, old_uri, new_uri, ctx=None, lock_handle=None):
        old_norm = self._norm(old_uri)
        new_norm = self._norm(new_uri)
        if old_norm in self._file_contents:
            self._file_contents[new_norm] = self._file_contents.pop(old_norm)
        return {"name": new_norm.rsplit("/", 1)[-1]}

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")

    def touch(self, uri: str, mod_time: str) -> None:
        self._mod_times[self._norm(uri)] = mod_time


# ---------- direct unit tests for the cache helpers ----------


@pytest.mark.asyncio
async def test_write_and_load_roundtrip():
    fs = _FakeVikingFS(tree={}, file_contents={})
    dir_uri = "viking://resources/d"
    entries = {"a.txt": {"content_hash": "size+mtime:11:T1", "embedded_at": "T1"}}
    await write_embedding_cache(fs, dir_uri, entries, ctx=None)

    final_path = f"{dir_uri}/{EMBEDDING_CACHE_FILENAME}"
    assert final_path in fs._file_contents
    payload = json.loads(fs._file_contents[final_path])
    assert payload["version"] == 1
    assert payload["entries"]["a.txt"]["content_hash"] == "size+mtime:11:T1"

    loaded = await load_embedding_cache(fs, dir_uri, ctx=None)
    assert loaded["a.txt"]["content_hash"] == "size+mtime:11:T1"


@pytest.mark.asyncio
async def test_load_returns_empty_for_missing_or_garbage():
    fs = _FakeVikingFS(tree={}, file_contents={})
    assert await load_embedding_cache(fs, "viking://resources/d", ctx=None) == {}

    fs._file_contents[f"viking://resources/d/{EMBEDDING_CACHE_FILENAME}"] = "{not json"
    assert await load_embedding_cache(fs, "viking://resources/d", ctx=None) == {}

    fs._file_contents[f"viking://resources/d/{EMBEDDING_CACHE_FILENAME}"] = json.dumps(
        {"version": 999, "entries": {"a.txt": {"content_hash": "x"}}}
    )
    assert await load_embedding_cache(fs, "viking://resources/d", ctx=None) == {}


@pytest.mark.asyncio
async def test_compute_cache_key_uses_size_and_modtime():
    fs = _FakeVikingFS(
        tree={},
        file_contents={"viking://resources/d/a.txt": "hello"},
    )
    fs.touch("viking://resources/d/a.txt", "2026-06-01T00:00:00Z")
    key = await compute_embedding_cache_key(fs, "viking://resources/d/a.txt", ctx=None)
    assert key == "size+mtime:5:2026-06-01T00:00:00Z"


@pytest.mark.asyncio
async def test_compute_cache_key_falls_back_to_sha256_when_stat_lacks_modtime():
    class _StatlessFS(_FakeVikingFS):
        async def stat(self, uri, ctx=None):
            return {"size": 5}  # no modTime

    fs = _StatlessFS(tree={}, file_contents={"viking://resources/d/a.txt": "hello"})
    key = await compute_embedding_cache_key(fs, "viking://resources/d/a.txt", ctx=None)
    assert key is not None and key.startswith("sha256:")


# ---------- integration test through SemanticDagExecutor ----------


class _FakeProcessor:
    def __init__(self, fs: _FakeVikingFS):
        self._fs = fs
        self.summarized_files: List[str] = []

    def _parse_overview_md(self, overview_content):
        # Mimic the buggy real-world case: free-text overview that yields no entries.
        if "FILES:" not in overview_content:
            return {}
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
        # Return a free-text overview that the parser will reject. This is
        # exactly the production failure mode for issue #2383.
        return "This directory has stuff in it."

    def _extract_abstract_from_overview(self, overview):
        return "abstract"

    def _enforce_size_limits(self, overview, abstract):
        return overview, abstract


def _make_executor(monkeypatch, fake_fs, processor, *, target_uri, changes=None):
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    # Vector backend probes default to "all absent" — tests that need a hit
    # patch this per-test.
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.probe_vectors_present",
        AsyncMock(return_value={}),
    )
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=target_uri,
        changes=changes or {},
    )
    return executor


@pytest.mark.asyncio
async def test_no_op_refresh_with_unparseable_overview_skips_embedding(monkeypatch):
    """Issue #2383: free-text overview must NOT trigger spurious embeds when sidecar exists."""
    _mock_transaction_layer(monkeypatch)
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}, {"name": "b.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "AAA",
            f"{root_uri}/b.txt": "BBB",
            f"{root_uri}/.overview.md": "This directory has stuff in it.",  # unparseable
            f"{root_uri}/.abstract.md": "old-abstract",
        },
    )
    fake_fs.touch(f"{root_uri}/a.txt", "T-A")
    fake_fs.touch(f"{root_uri}/b.txt", "T-B")
    # Pre-seed the embedding-cache sidecar (simulating a previous successful run).
    sidecar = {
        "version": 1,
        "entries": {
            "a.txt": {"content_hash": "size+mtime:3:T-A", "embedded_at": "prev"},
            "b.txt": {"content_hash": "size+mtime:3:T-B", "embedded_at": "prev"},
        },
    }
    fake_fs._file_contents[f"{root_uri}/{EMBEDDING_CACHE_FILENAME}"] = json.dumps(sidecar)

    processor = _FakeProcessor(fake_fs)
    executor = _make_executor(monkeypatch, fake_fs, processor, target_uri=root_uri)
    # All vectors present — cache hits are valid.
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.probe_vectors_present",
        AsyncMock(return_value={f"{root_uri}/a.txt": True, f"{root_uri}/b.txt": True}),
    )
    add_vec = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", add_vec)

    await executor.run(root_uri)

    # No file was vectorized (the bug would have re-embedded both).
    file_calls = [c for c in add_vec.await_args_list if c.args[0].task_type == "file"]
    assert len(file_calls) == 0, "embedding cache should have suppressed file re-embeds"


@pytest.mark.asyncio
async def test_modified_file_is_reembedded_others_skipped(monkeypatch):
    _mock_transaction_layer(monkeypatch)
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}, {"name": "b.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "AAA-NEW",  # changed since last embed
            f"{root_uri}/b.txt": "BBB",
            f"{root_uri}/.overview.md": "free text",
            f"{root_uri}/.abstract.md": "old",
        },
    )
    fake_fs.touch(f"{root_uri}/a.txt", "T-A2")
    fake_fs.touch(f"{root_uri}/b.txt", "T-B")
    sidecar = {
        "version": 1,
        "entries": {
            "a.txt": {"content_hash": "size+mtime:3:T-A", "embedded_at": "prev"},
            "b.txt": {"content_hash": "size+mtime:3:T-B", "embedded_at": "prev"},
        },
    }
    fake_fs._file_contents[f"{root_uri}/{EMBEDDING_CACHE_FILENAME}"] = json.dumps(sidecar)

    processor = _FakeProcessor(fake_fs)
    executor = _make_executor(
        monkeypatch,
        fake_fs,
        processor,
        target_uri=root_uri,
        changes={"modified": [f"{root_uri}/a.txt"]},
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.probe_vectors_present",
        AsyncMock(return_value={f"{root_uri}/a.txt": True, f"{root_uri}/b.txt": True}),
    )
    add_vec = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", add_vec)

    await executor.run(root_uri)

    file_calls = [c for c in add_vec.await_args_list if c.args[0].task_type == "file"]
    assert len(file_calls) == 1
    assert file_calls[0].args[0].file_path == f"{root_uri}/a.txt"


@pytest.mark.asyncio
async def test_missing_vectors_force_reembed_even_with_sidecar_hit(monkeypatch):
    _mock_transaction_layer(monkeypatch)
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "AAA",
            f"{root_uri}/.overview.md": "free text",
            f"{root_uri}/.abstract.md": "old",
        },
    )
    fake_fs.touch(f"{root_uri}/a.txt", "T-A")
    sidecar = {
        "version": 1,
        "entries": {"a.txt": {"content_hash": "size+mtime:3:T-A", "embedded_at": "prev"}},
    }
    fake_fs._file_contents[f"{root_uri}/{EMBEDDING_CACHE_FILENAME}"] = json.dumps(sidecar)

    processor = _FakeProcessor(fake_fs)
    executor = _make_executor(monkeypatch, fake_fs, processor, target_uri=root_uri)
    # Vector backend reports the URI is gone — sidecar must NOT lie.
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.probe_vectors_present",
        AsyncMock(return_value={f"{root_uri}/a.txt": False}),
    )
    add_vec = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", add_vec)

    await executor.run(root_uri)

    file_calls = [c for c in add_vec.await_args_list if c.args[0].task_type == "file"]
    assert len(file_calls) == 1
    assert file_calls[0].args[0].file_path == f"{root_uri}/a.txt"


@pytest.mark.asyncio
async def test_backward_compat_parseable_overview_still_skips(monkeypatch):
    """Pre-existing parseable .overview.md without a sidecar still benefits from the legacy skip."""
    _mock_transaction_layer(monkeypatch)
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={
            f"{root_uri}/a.txt": "AAA",
            f"{root_uri}/.overview.md": "FILES:\n- a.txt: prior summary",
            f"{root_uri}/.abstract.md": "old",
        },
    )
    fake_fs.touch(f"{root_uri}/a.txt", "T-A")
    # NO .embedding_cache.json on disk.

    processor = _FakeProcessor(fake_fs)
    executor = _make_executor(monkeypatch, fake_fs, processor, target_uri=root_uri)
    add_vec = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", add_vec)

    await executor.run(root_uri)

    file_calls = [c for c in add_vec.await_args_list if c.args[0].task_type == "file"]
    assert len(file_calls) == 0


@pytest.mark.asyncio
async def test_first_run_writes_sidecar(monkeypatch):
    _mock_transaction_layer(monkeypatch)
    root_uri = "viking://resources/root"
    tree = {root_uri: [{"name": "a.txt", "isDir": False}]}
    fake_fs = _FakeVikingFS(
        tree=tree,
        file_contents={f"{root_uri}/a.txt": "AAA"},
    )
    fake_fs.touch(f"{root_uri}/a.txt", "T-A")

    processor = _FakeProcessor(fake_fs)
    executor = _make_executor(monkeypatch, fake_fs, processor, target_uri=root_uri)
    add_vec = AsyncMock()
    monkeypatch.setattr(executor, "_add_vectorize_task", add_vec)

    await executor.run(root_uri)

    sidecar_path = f"{root_uri}/{EMBEDDING_CACHE_FILENAME}"
    assert sidecar_path in fake_fs._file_contents
    payload = json.loads(fake_fs._file_contents[sidecar_path])
    assert "a.txt" in payload["entries"]


if __name__ == "__main__":
    pytest.main([__file__])
