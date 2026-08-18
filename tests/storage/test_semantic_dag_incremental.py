# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import re
from unittest.mock import MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.task_work_index import TaskExecutionContext, get_task_context
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
        self.file_task_ids = {}
        self.directory_options = []
        self.overview_calls = 0

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
        self.overview_calls += 1
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
        self.vectorized_files.append((file_path, ingest_options))
        self.file_task_ids[file_path] = get_task_context().task_id

    async def _vectorize_directory(
        self,
        uri,
        context_type,
        abstract,
        overview,
        ctx=None,
        ingest_options=None,
    ):
        self.directory_options.append(ingest_options)

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
            f"{root_uri}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
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
        changes={"modified": [f"{root_uri}/a.txt", f"{root_uri}/b.txt"]},
        file_ingest_options={
            f"{root_uri}/a.txt": IngestOptions(search_tags=["file=a"]),
            f"{root_uri}/b.txt": IngestOptions(search_tags=["file=b"]),
        },
        file_task_contexts={
            f"{root_uri}/a.txt": TaskExecutionContext("task-a", "acc1", "user1"),
            f"{root_uri}/b.txt": TaskExecutionContext("task-b", "acc1", "user1"),
        },
        directory_ingest_options=IngestOptions(search_tags=["file=b"]),
    )

    await executor.run(root_uri)

    assert set(processor.summarized_files) == {
        f"{root_uri}/a.txt",
        f"{root_uri}/b.txt",
    }
    assert dict(processor.vectorized_files) == {
        f"{root_uri}/a.txt": IngestOptions(search_tags=["file=a"]),
        f"{root_uri}/b.txt": IngestOptions(search_tags=["file=b"]),
    }
    assert processor.file_task_ids == {
        f"{root_uri}/a.txt": "task-a",
        f"{root_uri}/b.txt": "task-b",
    }
    assert processor.overview_calls == 1
    assert processor.directory_options == [IngestOptions(search_tags=["file=b"])]
    assert processor.sync_calls == []
    overview = fake_fs._file_contents[f"{root_uri}/.overview.md"]
    assert "- a.txt: summary" in overview
    assert "- b.txt: summary" in overview


if __name__ == "__main__":
    pytest.main([__file__])
