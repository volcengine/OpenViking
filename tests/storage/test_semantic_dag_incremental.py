# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.storage.semantic_sidecar import (
    SemanticSidecarFormatError,
    parse_semantic_sidecar,
    render_semantic_sidecar,
)
from openviking_cli.session.user_id import UserIdentifier


class _FakeVikingFS:
    def __init__(self, tree, file_contents):
        self._tree = {self._norm(k): v for k, v in tree.items()}
        self._file_contents = {self._norm(k): v for k, v in file_contents.items()}
        self.writes = []
        self._async_agfs = self

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

    async def write_file(self, path, content, ctx=None, lease_ref=None):
        norm_path = self._norm(path)
        self._file_contents[norm_path] = content
        self.writes.append((norm_path, content))

    async def pathlock_acquire_exact_batch(self, paths):
        return {"paths": paths}

    async def pathlock_release(self, lease):
        return None

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _FakeProcessor:
    def __init__(self, viking_fs):
        self._fs = viking_fs
        self.summarized_files = []
        self.sync_calls = []
        self.vectorized_files = []
        self.overview_inputs = []

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
        self.overview_inputs.append((dir_uri, list(file_summaries), list(children_abstracts)))
        lines = ["FILES:"]
        for item in file_summaries:
            name = item.get("name", "")
            summary = item.get("summary", "")
            lines.append(f"- {name}: {summary}")
        return "\n".join(lines)

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

    async def _vectorize_single_file(
        self,
        parent_uri,
        context_type,
        file_path,
        summary_dict,
        ctx=None,
        use_summary=False,
        ingest_options=None,
    ):
        self.vectorized_files.append(file_path)

    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        ingest_options=None,
    ):
        return None

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


@pytest.mark.asyncio
async def test_direct_incremental_update_uses_changes_without_temp_sync(monkeypatch):

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
            f"{root_uri}/.overview.md": render_semantic_sidecar(
                ContextLevel.OVERVIEW,
                root_uri,
                "FILES:\n- a.txt: old-a\n- b.txt: old-b",
                {
                    "generated_by": {
                        "component": "SemanticProcessor",
                        "trigger": "previous_refresh",
                    }
                },
            ),
            f"{root_uri}/.abstract.md": "old-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=root_uri,
        changes={"modified": [f"{root_uri}/a.txt"]},
    )

    await executor.run(root_uri)

    assert processor.summarized_files == [f"{root_uri}/a.txt"]
    assert processor.vectorized_files == [f"{root_uri}/a.txt"]
    assert processor.sync_calls == []
    overview = fake_fs._file_contents[f"{root_uri}/.overview.md"]
    assert "- a.txt: summary" in overview
    assert "- b.txt: old-b" in overview


@pytest.mark.asyncio
async def test_large_directory_sampling_is_deterministic_and_stable(monkeypatch):
    root_uri = "viking://resources/root"
    entries = [{"name": f"file-{index}.txt", "isDir": False} for index in reversed(range(5))]
    fake_fs = _FakeVikingFS(
        tree={root_uri: entries},
        file_contents={f"{root_uri}/file-{index}.txt": str(index) for index in range(5)},
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(sidecar_sample_size=2)),
    )
    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)

    async def run_once():
        await SemanticDagExecutor(
            processor=processor,
            context_type="resource",
            max_concurrent_llm=2,
            ctx=ctx,
            source={"kind": "http", "uri": "https://example.com/root.zip"},
            generation_trigger="resource_ingest",
            skip_vectorization=True,
        ).run(root_uri)

    await run_once()
    sampled_names = [item["name"] for item in processor.overview_inputs[-1][1]]
    assert sampled_names == ["file-0.txt", "file-4.txt"]
    document = parse_semantic_sidecar(fake_fs._file_contents[f"{root_uri}/.overview.md"])
    assert document.metadata["source"] == {
        "kind": "http",
        "uri": "https://example.com/root.zip",
    }
    assert document.metadata["generated_by"] == {
        "component": "SemanticProcessor",
        "trigger": "resource_ingest",
    }
    assert document.metadata["freshness"] == {
        "total_entries": 5,
        "sampled_entries": 2,
        "unsampled_entries": 3,
        "pending_child_changes": 0,
    }

    fake_fs.writes.clear()
    await run_once()
    assert [item["name"] for item in processor.overview_inputs[-1][1]] == sampled_names
    assert fake_fs.writes == []


@pytest.mark.asyncio
async def test_source_metadata_is_only_written_on_import_root(monkeypatch):
    root_uri = "viking://resources/root"
    child_uri = f"{root_uri}/child"
    fake_fs = _FakeVikingFS(
        tree={
            root_uri: [{"name": "child", "isDir": True}],
            child_uri: [{"name": "note.txt", "isDir": False}],
        },
        file_contents={f"{child_uri}/note.txt": "note"},
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)

    await SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        source={"kind": "git", "uri": "https://example.com/repo.git"},
        generation_trigger="resource_ingest",
        skip_vectorization=True,
    ).run(root_uri)

    root = parse_semantic_sidecar(fake_fs._file_contents[f"{root_uri}/.overview.md"])
    child = parse_semantic_sidecar(fake_fs._file_contents[f"{child_uri}/.overview.md"])
    assert root.metadata["source"]["kind"] == "git"
    assert "source" not in child.metadata
    root_inputs = next(
        children for dir_uri, _files, children in processor.overview_inputs if dir_uri == root_uri
    )
    assert root_inputs == [{"name": "child", "abstract": "abstract"}]
    assert "directory:" not in root_inputs[0]["abstract"]


@pytest.mark.asyncio
async def test_malformed_incremental_sidecar_fails_loudly(monkeypatch):
    root_uri = "viking://resources/root"
    file_uri = f"{root_uri}/a.txt"
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": "a.txt", "isDir": False}]},
        file_contents={
            file_uri: "unchanged",
            f"{root_uri}/.overview.md": "---\ndirectory: [broken\n---\nbody",
            f"{root_uri}/.abstract.md": "legacy",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)

    with pytest.raises(SemanticSidecarFormatError):
        await SemanticDagExecutor(
            processor=processor,
            context_type="resource",
            max_concurrent_llm=1,
            ctx=ctx,
            incremental_update=True,
            target_uri=root_uri,
            changes={"modified": []},
            skip_vectorization=True,
        ).run(root_uri)


if __name__ == "__main__":
    pytest.main([__file__])
