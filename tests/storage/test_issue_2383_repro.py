# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.session.user_id import UserIdentifier


class _FakeAgfs:
    async def pathlock_acquire_exact_batch(self, _paths):
        return {"lease_ref": "test"}

    async def pathlock_release(self, _lease):
        return None


class _FakeVikingFS:
    def __init__(self, contents, entries):
        self.contents = dict(contents)
        self.entries = {k: list(v) for k, v in entries.items()}
        self.mutations = []
        self.deleted_temp = []
        self._async_agfs = _FakeAgfs()

    async def exists(self, uri, ctx=None):
        return uri in self.entries

    async def ls(self, uri, show_all_hidden=False, node_limit=None, ctx=None):
        out = []
        for entry in self.entries.get(uri, []):
            name = entry.get("name", "")
            if show_all_hidden or not name.startswith("."):
                out.append(entry)
        return out

    async def stat(self, uri, ctx=None):
        content = self.contents.get(uri, "")
        return {"size": len(content)}

    async def read_file(self, uri, ctx=None):
        return self.contents.get(uri, "")

    async def rm(self, uri, recursive=False, ctx=None, lease_ref=None):
        self.mutations.append(("rm", uri, lease_ref))
        self.contents.pop(uri, None)
        for parent, items in list(self.entries.items()):
            self.entries[parent] = [e for e in items if _entry_uri(parent, e) != uri]

    async def mv(self, src, dst, ctx=None, lease_ref=None):
        self.mutations.append(("mv", src, dst, lease_ref))
        self.contents[dst] = self.contents.pop(src)
        src_parent = _parent_uri(src)
        dst_parent = _parent_uri(dst)
        name = dst.rsplit("/", 1)[-1]
        if src_parent in self.entries:
            self.entries[src_parent] = [e for e in self.entries[src_parent] if e.get("name") != _name(src)]
        self.entries.setdefault(dst_parent, []).append({"name": name, "isDir": False})

    async def mkdir(self, uri, exist_ok=False, ctx=None, lease_ref=None):
        self.mutations.append(("mkdir", uri, lease_ref))
        self.entries.setdefault(uri, [])

    async def delete_temp(self, uri, ctx=None, lease_ref=None):
        self.mutations.append(("delete_temp", uri, lease_ref))
        self.deleted_temp.append(uri)

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/")


def _name(uri):
    return uri.rsplit("/", 1)[-1]


def _parent_uri(uri):
    if "/" not in uri.split("://", 1)[-1]:
        return None
    return uri.rsplit("/", 1)[0]


def _entry_uri(parent, entry):
    return parent.rstrip("/") + "/" + entry["name"]


class _FakeProcessor(SemanticProcessor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.files_summarized = []
        self.overviews_generated = []
        self.vectorized_files = []
        self.vectorized_dirs = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        self.files_summarized.append(file_path)
        return {"name": file_path.split("/")[-1], "summary": f"summary:{file_path.split('/')[-1]}"}

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        self.overviews_generated.append(dir_uri)
        lines = ["FILES:"]
        for item in file_summaries:
            lines.append(f"- {item['name']}: {item['summary']}")
        for item in children_abstracts:
            lines.append(f"- {item['name']}: {item['abstract']}")
        return "\n".join(lines)

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

    async def _vectorize_single_file(self, parent_uri, context_type, file_path, summary_dict, ctx=None, semantic_msg_id=None, use_summary=False, ingest_options=None):
        self.vectorized_files.append(file_path)

    async def _vectorize_directory(self, dir_uri, context_type, abstract, overview, ctx=None, semantic_msg_id=None, ingest_options=None):
        self.vectorized_dirs.append(dir_uri)

    def _parse_overview_md(self, overview_content):
        import re
        results = {}
        for line in overview_content.splitlines():
            m = re.match(r"^-\s*(?P<name>[^:]+):\s*(?P<summary>.*)$", line.strip())
            if not m:
                continue
            results[m.group("name").strip()] = m.group("summary").strip()
        return results

    async def _rewrite_target_image_uris(self, root_uri, target_uri, ctx=None, lock=None):
        return None


@pytest.mark.asyncio
async def test_unchanged_temp_target_refresh_skips_all_embedding_work(monkeypatch):
    root = "viking://resources/root"
    temp = "viking://temp/refresh/root"
    contents = {
        f"{temp}/a.txt": "A content",
        f"{temp}/b.txt": "B content",
        f"{root}/a.txt": "A content",
        f"{root}/b.txt": "B content",
        f"{root}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
        f"{root}/.abstract.md": "old-abstract",
    }
    entries = {
        temp: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
        ],
        root: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
            {"name": ".overview.md", "isDir": False},
            {"name": ".abstract.md", "isDir": False},
        ],
    }
    fs = _FakeVikingFS(contents, entries)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.write_semantic_sidecars",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock={"lease_ref": "test"}, close=AsyncMock())),
    )

    processor = _FakeProcessor()
    processor._default_ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    processor._enqueue_parent_refresh = AsyncMock()
    msg = SemanticMsg(
        uri=temp,
        target_uri=root,
        context_type="resource",
        account_id="acc1",
        user_id="user1",
        peer_id="user1",
        role=str(Role.USER),
    )

    await processor.on_dequeue(msg.to_dict())

    assert processor.files_summarized == [], processor.files_summarized
    assert processor.overviews_generated == [], processor.overviews_generated
    assert processor.vectorized_files == [], processor.vectorized_files
    assert processor.vectorized_dirs == [], processor.vectorized_dirs


@pytest.mark.asyncio
async def test_unchanged_temp_target_sync_skips_semantic_and_embedding_work_end_to_end(monkeypatch):
    """Reproduces #2383: repeated add_resource where temp tree matches target.

    In the real add_resource flow, the parser produces a fresh viking://temp
    tree and Summarizer emits SemanticMsg(uri=temp, target_uri=root).
    SemanticProcessor must sync temp→target via _sync_topdown_recursive,
    detect zero diff, and run the DAG in incremental mode so no file
    summaries, overviews or vectors are regenerated for unchanged content.
    """
    root = "viking://resources/root"
    temp = "viking://temp/refresh/root"
    contents = {
        f"{temp}/a.txt": "A content",
        f"{temp}/b.txt": "B content",
        f"{root}/a.txt": "A content",
        f"{root}/b.txt": "B content",
        f"{root}/.overview.md": "FILES:\n- a.txt: old-a\n- b.txt: old-b",
        f"{root}/.abstract.md": "old-abstract",
        f"{temp}/.image_mappings.json": "{}",
    }
    entries = {
        temp: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
            {"name": ".image_mappings.json", "isDir": False},
        ],
        root: [
            {"name": "a.txt", "isDir": False},
            {"name": "b.txt", "isDir": False},
            {"name": ".overview.md", "isDir": False},
            {"name": ".abstract.md", "isDir": False},
        ],
    }
    fs = _FakeVikingFS(contents, entries)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_processor.get_viking_fs", lambda: fs)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.write_semantic_sidecars",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_processor.SemanticLockScope.resolve",
        AsyncMock(return_value=SimpleNamespace(lock={"lease_ref": "test"}, close=AsyncMock())),
    )

    processor = _FakeProcessor()
    processor._default_ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    processor._enqueue_parent_refresh = AsyncMock()
    processor._rewrite_target_image_uris = AsyncMock()
    msg = SemanticMsg(
        uri=temp,
        target_uri=root,
        context_type="resource",
        account_id="acc1",
        user_id="user1",
        peer_id="user1",
        role=str(Role.USER),
        target_preexisting=True,
    )

    await processor.on_dequeue(msg.to_dict())

    assert processor.files_summarized == [], processor.files_summarized
    assert processor.overviews_generated == [], processor.overviews_generated
    assert processor.vectorized_files == [], processor.vectorized_files
    assert processor.vectorized_dirs == [], processor.vectorized_dirs


if __name__ == "__main__":
    pytest.main([__file__])
