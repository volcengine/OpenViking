# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage.abstract_overview import (
    deterministic_sample,
    freshness_metadata,
    parse_abstract_overview,
    render_abstract_overview,
)
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.utils.ingest_options import IngestOptions
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

    async def abstract(self, uri, ctx=None):
        return self._file_contents.get(
            self._norm(f"{uri}/.abstract.md"),
            f"# {uri} [Directory abstract is not ready]",
        )

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
    def __init__(self, viking_fs, transfer_summaries=None):
        self._fs = viking_fs
        self.transfer_summaries = transfer_summaries or {}
        self.transfer_summary_calls = []
        self.summarized_files = []
        self.sync_calls = []
        self.vectorized_files = []
        self.file_ingest_options = {}
        self.directory_ingest_options = {}
        self.vectorized_dirs = []
        self.generated_overviews = []

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

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts, **kwargs):
        self.generated_overviews.append(dir_uri)
        lines = ["FILES:"]
        for item in file_summaries:
            name = item.get("name", "")
            summary = item.get("summary", "")
            lines.append(f"- {name}: {summary}")
        for item in children_abstracts:
            name = item.get("name", "")
            abstract = item.get("abstract", "")
            lines.append(f"- {name}/: {abstract}")
        return "\n".join(lines)

    async def _load_transfer_file_summaries(self, file_paths, ctx=None):
        self.transfer_summary_calls.append(list(file_paths))
        return {
            path: self.transfer_summaries[path]
            for path in file_paths
            if self.transfer_summaries.get(path)
        }

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
        creator_acl_grant=None,
    ):
        del creator_acl_grant
        self.vectorized_files.append(file_path)
        self.file_ingest_options[file_path] = ingest_options

    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        ingest_options=None,
        creator_acl_grant=None,
    ):
        del creator_acl_grant
        self.directory_ingest_options[uri] = ingest_options
        self.vectorized_dirs.append(uri)
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
            f"{root_uri}/.overview.md": render_abstract_overview(
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
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

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
    overview = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"]).body
    assert "- a.txt: summary" in overview
    assert "- b.txt: old-b" in overview


@pytest.mark.asyncio
async def test_content_write_tags_apply_only_to_changed_file(monkeypatch):
    root_uri = "viking://resources/root"
    changed_uri = f"{root_uri}/a.txt"
    fake_fs = _FakeVikingFS(
        tree={
            root_uri: [
                {"name": "a.txt", "isDir": False},
                {"name": "b.txt", "isDir": False},
            ],
        },
        file_contents={
            changed_uri: "new content",
            f"{root_uri}/b.txt": "unchanged",
            f"{root_uri}/.overview.md": render_abstract_overview(
                ContextLevel.OVERVIEW,
                root_uri,
                "FILES:\n- a.txt: old-a\n- b.txt: old-b",
                {},
            ),
            f"{root_uri}/.abstract.md": "old-abstract",
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    tag_options = IngestOptions(search_tags=["team=search"])
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=root_uri,
        changes={"modified": [changed_uri]},
        ingest_options=tag_options,
        generation_trigger="content_write",
    )

    await executor.run(root_uri)

    assert processor.file_ingest_options == {changed_uri: tag_options}
    assert processor.directory_ingest_options[root_uri] == IngestOptions()


@pytest.mark.asyncio
async def test_pending_refresh_rebuilds_every_sampled_file_summary(monkeypatch):
    root_uri = "viking://resources/wide"
    file_names = [f"file-{idx:03}.txt" for idx in range(40)]
    file_paths = [f"{root_uri}/{name}" for name in file_names]
    sampled_paths = deterministic_sample(file_paths, 4)
    changed_path = f"{root_uri}/file-020.txt"
    old_overview = "FILES:\n" + "\n".join(f"- {name}: old-summary" for name in file_names)
    metadata = {"freshness": freshness_metadata(40, 4, pending=4)}
    fake_fs = _FakeVikingFS(
        tree={
            root_uri: [{"name": name, "isDir": False} for name in file_names],
        },
        file_contents={
            **dict.fromkeys(file_paths, "content"),
            f"{root_uri}/.overview.md": render_abstract_overview(
                ContextLevel.OVERVIEW, root_uri, old_overview, metadata
            ),
            f"{root_uri}/.abstract.md": render_abstract_overview(
                ContextLevel.ABSTRACT, root_uri, "old abstract", metadata
            ),
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=4)),
    )

    processor = _FakeProcessor(fake_fs)
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=ctx,
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"modified": [changed_path]},
    )

    await executor.run(root_uri)

    assert set(processor.summarized_files) == set(sampled_paths) | {changed_path}
    assert processor.vectorized_files == [changed_path]
    overview = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"])
    assert overview.metadata["freshness"]["pending_child_changes"] == 0


@pytest.mark.asyncio
async def test_directory_vectorization_retries_after_matching_sidecar_write(monkeypatch):
    root_uri = "viking://resources/root"
    file_path = f"{root_uri}/a.txt"
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": "a.txt", "isDir": False}]},
        file_contents={
            file_path: "new content",
            f"{root_uri}/.overview.md": render_abstract_overview(
                ContextLevel.OVERVIEW, root_uri, "FILES:\n- a.txt: old-summary"
            ),
            f"{root_uri}/.abstract.md": render_abstract_overview(
                ContextLevel.ABSTRACT, root_uri, "old abstract"
            ),
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )

    processor = _FakeProcessor(fake_fs)
    vectorize_directory = AsyncMock(side_effect=[RuntimeError("temporary failure"), None])
    processor._vectorize_directory = vectorize_directory
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)

    def make_executor():
        return SemanticDagExecutor(
            processor=processor,
            context_type="resource",
            max_concurrent_llm=2,
            ctx=ctx,
            incremental_update=True,
            target_uri=root_uri,
            recursive=False,
            changes={"modified": [file_path]},
        )

    with pytest.raises(RuntimeError, match="temporary failure"):
        await make_executor().run(root_uri)
    await make_executor().run(root_uri)

    assert vectorize_directory.await_count == 2


@pytest.mark.asyncio
async def test_content_copy_rebuilds_target_overview_from_target_l2_summaries(monkeypatch):
    root_uri = "viking://resources/archive"
    copied_uri = f"{root_uri}/copied.jpg"
    fake_fs = _FakeVikingFS(
        tree={
            root_uri: [
                {"name": "copied.jpg", "isDir": False},
                {"name": "keep.txt", "isDir": False},
            ]
        },
        file_contents={
            copied_uri: "binary-placeholder",
            f"{root_uri}/keep.txt": "keep",
            f"{root_uri}/.overview.md": render_abstract_overview(
                ContextLevel.OVERVIEW,
                root_uri,
                "FILES:\n- keep.txt: existing summary",
            ),
            f"{root_uri}/.abstract.md": render_abstract_overview(
                ContextLevel.ABSTRACT,
                root_uri,
                "existing abstract",
            ),
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )
    processor = _FakeProcessor(
        fake_fs,
        transfer_summaries={
            copied_uri: "copied target L2 summary",
            f"{root_uri}/keep.txt": "kept target L2 summary",
        },
    )
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [copied_uri]},
        skip_vectorization=False,
        generation_trigger="content_copy",
        copy_source_uri="viking://resources/source/original.jpg",
    )

    await executor.run(root_uri)

    assert processor.summarized_files == []
    assert processor.vectorized_files == []
    assert processor.generated_overviews == [root_uri]
    assert processor.vectorized_dirs == [root_uri]
    overview = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"]).body
    abstract = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.abstract.md"]).body
    assert "- copied.jpg: copied target L2 summary" in overview
    assert "- keep.txt: kept target L2 summary" in overview
    assert abstract.strip() == "abstract"


@pytest.mark.asyncio
async def test_content_copy_samples_before_loading_summaries(monkeypatch):
    root_uri = "viking://resources/archive"
    file_paths = [f"{root_uri}/{name}.txt" for name in "abcde"]
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": path.rsplit("/", 1)[-1], "isDir": False} for path in file_paths]},
        file_contents={path: path for path in file_paths},
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=2)),
    )
    processor = _FakeProcessor(
        fake_fs,
        transfer_summaries={path: f"summary-{path.rsplit('/', 1)[-1]}" for path in file_paths},
    )
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [file_paths[2]]},
        generation_trigger="content_copy",
    )

    await executor.run(root_uri)

    assert processor.transfer_summary_calls == [[file_paths[0], file_paths[-1]]]
    assert processor.summarized_files == []
    overview = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"]).body
    assert "a.txt" in overview
    assert "e.txt" in overview
    assert "c.txt" not in overview


@pytest.mark.asyncio
async def test_content_copy_does_not_backfill_missing_sample_summary(monkeypatch):
    root_uri = "viking://resources/archive"
    file_paths = [f"{root_uri}/{name}.txt" for name in "abcde"]
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": path.rsplit("/", 1)[-1], "isDir": False} for path in file_paths]},
        file_contents={path: path for path in file_paths},
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=2)),
    )
    processor = _FakeProcessor(
        fake_fs,
        transfer_summaries={path: f"summary-{path.rsplit('/', 1)[-1]}" for path in file_paths[1:]},
    )
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [file_paths[2]]},
        generation_trigger="content_copy",
    )

    await executor.run(root_uri)

    assert processor.transfer_summary_calls == [[file_paths[0], file_paths[-1]]]
    overview_doc = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"])
    assert "a.txt" not in overview_doc.body
    assert "b.txt" not in overview_doc.body
    assert "e.txt" in overview_doc.body
    assert overview_doc.metadata["freshness"].get("missing_summary_entries") is None


@pytest.mark.asyncio
async def test_content_copy_with_no_ready_summaries_preserves_existing_sidecars(monkeypatch):
    root_uri = "viking://resources/archive"
    file_path = f"{root_uri}/pending.txt"
    old_overview = render_abstract_overview(ContextLevel.OVERVIEW, root_uri, "old overview")
    old_abstract = render_abstract_overview(ContextLevel.ABSTRACT, root_uri, "old abstract")
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": "pending.txt", "isDir": False}]},
        file_contents={
            file_path: "pending",
            f"{root_uri}/.overview.md": old_overview,
            f"{root_uri}/.abstract.md": old_abstract,
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )
    processor = _FakeProcessor(fake_fs)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [file_path]},
        generation_trigger="content_copy",
    )

    await executor.run(root_uri)

    assert processor.generated_overviews == []
    assert processor.vectorized_dirs == []
    assert fake_fs._file_contents[f"{root_uri}/.overview.md"] == old_overview
    assert fake_fs._file_contents[f"{root_uri}/.abstract.md"] == old_abstract


@pytest.mark.asyncio
async def test_content_copy_rebuilds_semantics_when_move_leaves_source_directory_empty(monkeypatch):
    root_uri = "viking://resources/source"
    old_overview = render_abstract_overview(ContextLevel.OVERVIEW, root_uri, "old file summary")
    old_abstract = render_abstract_overview(ContextLevel.ABSTRACT, root_uri, "old abstract")
    fake_fs = _FakeVikingFS(
        tree={root_uri: []},
        file_contents={
            f"{root_uri}/.overview.md": old_overview,
            f"{root_uri}/.abstract.md": old_abstract,
        },
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )
    processor = _FakeProcessor(fake_fs)
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"deleted": [f"{root_uri}/moved.txt"]},
        generation_trigger="content_copy",
    )

    await executor.run(root_uri)

    assert processor.generated_overviews == [root_uri]
    assert processor.vectorized_dirs == [root_uri]
    overview = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.overview.md"]).body
    abstract = parse_abstract_overview(fake_fs._file_contents[f"{root_uri}/.abstract.md"]).body
    assert overview.strip() == "FILES:"
    assert abstract.strip() == "abstract"


@pytest.mark.asyncio
async def test_content_copy_propagates_vector_summary_read_failure(monkeypatch):
    root_uri = "viking://resources/archive"
    file_path = f"{root_uri}/copied.txt"
    fake_fs = _FakeVikingFS(
        tree={root_uri: [{"name": "copied.txt", "isDir": False}]},
        file_contents={file_path: "copied"},
    )
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.get_openviking_config",
        lambda: SimpleNamespace(semantic=SimpleNamespace(overview_sample_limit=32)),
    )
    processor = _FakeProcessor(fake_fs)

    async def fail_summary_read(file_paths, ctx=None):
        raise RuntimeError("vector backend unavailable")

    processor._load_transfer_file_summaries = fail_summary_read
    executor = SemanticDagExecutor(
        processor=processor,
        context_type="resource",
        max_concurrent_llm=2,
        ctx=RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER),
        incremental_update=True,
        target_uri=root_uri,
        recursive=False,
        changes={"added": [file_path]},
        generation_trigger="content_copy",
    )

    with pytest.raises(RuntimeError, match="vector backend unavailable"):
        await executor.run(root_uri)


if __name__ == "__main__":
    pytest.main([__file__])
