# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Pipeline wiring for the substantive-content gate (issue #3028).

Table 2 of .wiki/issue-3028-substantive-content-gate-plan.md §7.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage.queuefs import semantic_processor as sp
from openviking.storage.queuefs.semantic_dag import SemanticDagExecutor
from openviking.storage.queuefs.semantic_processor import (
    SemanticProcessor,
    _neutral_directory_overview,
    is_neutral_overview,
)
from openviking_cli.session.user_id import UserIdentifier

HEADING_ONLY = "# Example Wiki Page\n## Subheading\n"
SUBSTANTIVE = "# Install\n\nRun `make build` to compile the project locally.\n"


def _fake_config(vlm):
    return SimpleNamespace(
        vlm=vlm,
        semantic=SimpleNamespace(
            min_substantive_chars=8,
            max_file_content_chars=100000,
            max_overview_prompt_chars=100000,
            overview_batch_size=50,
            overview_max_chars=4000,
            abstract_max_chars=256,
        ),
    )


class _FakeFS:
    def __init__(self, contents):
        self._contents = contents

    async def read_file(self, path, ctx=None):
        return self._contents[path]


# --------------------------------------------------------------------------- #
# Point 1 — VLM short-circuit in _generate_text_summary
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_nonsubstantive_file_skips_vlm(monkeypatch):
    vlm = MagicMock()
    vlm.is_available.return_value = True
    vlm.get_completion_async = AsyncMock(return_value="hallucinated")
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")
    path = "viking://user/u/docs/empty.md"
    monkeypatch.setattr(sp, "get_viking_fs", lambda: _FakeFS({path: HEADING_ONLY}))

    processor = SemanticProcessor()
    result = await processor._generate_text_summary(path, "empty.md", MagicMock())

    vlm.get_completion_async.assert_not_awaited()
    assert result["summary"] == ""
    assert result["has_substantive_content"] is False


@pytest.mark.asyncio
async def test_substantive_file_calls_vlm_and_flags_true(monkeypatch):
    vlm = MagicMock()
    vlm.is_available.return_value = True
    vlm.get_completion_async = AsyncMock(return_value="a real summary")
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.resolve_output_language", lambda *a, **k: "en"
    )
    path = "viking://user/u/docs/install.md"
    monkeypatch.setattr(sp, "get_viking_fs", lambda: _FakeFS({path: SUBSTANTIVE}))

    processor = SemanticProcessor()
    result = await processor._generate_text_summary(path, "install.md", MagicMock())

    vlm.get_completion_async.assert_awaited_once()
    assert result["summary"] == "a real summary"
    assert result["has_substantive_content"] is True


# --------------------------------------------------------------------------- #
# Point 4 — overview filters non-substantive summaries
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_overview_filters_nonsubstantive(monkeypatch):
    vlm = MagicMock()
    vlm.is_available.return_value = True
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.resolve_output_language", lambda *a, **k: "en"
    )

    captured = {}

    async def _fake_single(
        dir_uri, file_summaries_str, children_abstracts_str, file_index_map, **k
    ):
        captured["summaries"] = file_summaries_str
        return "overview"

    processor = SemanticProcessor()
    monkeypatch.setattr(processor, "_single_generate_overview", _fake_single)

    summaries = [
        {"name": "substantive.md", "summary": "real", "has_substantive_content": True},
        {"name": "heading_only.md", "summary": "", "has_substantive_content": False},
    ]
    await processor._generate_overview("viking://user/u/docs", summaries, [])

    assert "substantive.md" in captured["summaries"]
    assert "heading_only.md" not in captured["summaries"]


# --------------------------------------------------------------------------- #
# Point 4 — all-non-substantive dir yields the neutral overview without the VLM
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_all_nonsubstantive_dir_writes_neutral_overview(monkeypatch):
    vlm = MagicMock()
    vlm.is_available.return_value = True
    vlm.get_completion_async = AsyncMock()
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")

    processor = SemanticProcessor()
    dir_uri = "viking://user/u/docs"
    summaries = [
        {"name": "a.md", "summary": "", "has_substantive_content": False},
        {"name": "b.md", "summary": "", "has_substantive_content": False},
    ]
    overview = await processor._generate_overview(dir_uri, summaries, [])

    vlm.get_completion_async.assert_not_awaited()
    assert overview == _neutral_directory_overview("docs")

    overview_norm, abstract = processor._normalize_overview_generation(overview)
    assert overview_norm == overview
    assert abstract == "[Directory has no substantive content]"


@pytest.mark.asyncio
async def test_vlm_unavailable_with_substantive_content_is_not_no_content(monkeypatch):
    # VLM down is transient — a directory WITH substantive summaries must not be
    # mislabeled "no substantive content" (which would be treated as permanently
    # empty and non-embeddable). It gets the transient not-ready marker instead.
    vlm = MagicMock()
    vlm.is_available.return_value = False
    vlm.get_completion_async = AsyncMock()
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(vlm))
    monkeypatch.setattr(sp, "render_prompt", lambda *a, **k: "prompt")

    processor = SemanticProcessor()
    summaries = [{"name": "a.md", "summary": "real content", "has_substantive_content": True}]
    overview = await processor._generate_overview("viking://user/u/docs", summaries, [])

    vlm.get_completion_async.assert_not_awaited()
    assert overview != _neutral_directory_overview("docs")
    assert is_neutral_overview(overview) is False
    assert "[Directory overview is not ready]" in overview


# --------------------------------------------------------------------------- #
# Point 5 backstop — neutral overview is recognized as non-embeddable
# --------------------------------------------------------------------------- #
def test_neutral_overview_matches_not_ready_sentinel():
    from openviking.service.reindex_executor import (
        _NO_SUBSTANTIVE_CONTENT_SUFFIX,
        _is_not_ready_sentinel,
    )

    overview = _neutral_directory_overview("docs")
    assert overview.endswith("[Directory has no substantive content]")
    assert is_neutral_overview(overview) is True
    # The reindex embedding guard refuses the distinct no-content marker too.
    assert _is_not_ready_sentinel(overview, _NO_SUBSTANTIVE_CONTENT_SUFFIX) is True


# --------------------------------------------------------------------------- #
# DAG wiring — points 3 (file vectorize skip) and 5 (dir vectorize skip)
# --------------------------------------------------------------------------- #
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
    monkeypatch.setattr("openviking.storage.transaction.get_lock_manager", lambda: MagicMock())


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []
        self.deleted = []

    async def ls(self, uri, node_limit=None, ctx=None):
        return self._tree.get(uri, [])

    async def write_file(self, path, content, ctx=None, lock_handle=None):
        self.writes.append((path, content))

    async def _delete_from_vector_store(self, uris, ctx=None):
        self.deleted.extend(uris)

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _GateFakeProcessor:
    """Returns a substantive/non-substantive summary per file name and a neutral
    overview when nothing substantive remains — exercises the DAG gates."""

    def __init__(self):
        self.vectorized_files = []
        self.vectorized_dirs = []
        # name -> bool; overrides the "sub in name" heuristic. Lets a test
        # "rewrite" a file from substantive to non-substantive between runs.
        self.substantive_override = {}

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        name = file_path.split("/")[-1]
        substantive = self.substantive_override.get(name, "sub" in name)
        return {
            "name": name,
            "summary": "s" if substantive else "",
            "has_substantive_content": substantive,
        }

    async def _generate_overview(self, dir_uri, file_summaries, children_abstracts):
        substantive = [s for s in file_summaries if s.get("has_substantive_content", True)]
        if not substantive and not children_abstracts:
            return _neutral_directory_overview(dir_uri.split("/")[-1])
        return "overview"

    def _normalize_overview_generation(self, overview):
        return overview, "abstract"

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


class _DummyTracker:
    async def register(self, **_kwargs):
        return None


def _run_executor(monkeypatch, tree, root_uri, processor=None, fake_fs=None):
    _mock_transaction_layer(monkeypatch)
    fake_fs = fake_fs or _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyTracker(),
    )
    processor = processor or _GateFakeProcessor()
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    executor = SemanticDagExecutor(
        processor=processor, context_type="session", max_concurrent_llm=2, ctx=ctx
    )
    return processor, fake_fs, executor


@pytest.mark.asyncio
async def test_nonsubstantive_file_not_vectorized(monkeypatch):
    root_uri = "viking://session/test-session"
    tree = {
        root_uri: [
            {"name": "substantive.md", "isDir": False},
            {"name": "heading_only.md", "isDir": False},
        ],
    }
    processor, _fs, executor = _run_executor(monkeypatch, tree, root_uri)
    await executor.run(root_uri)

    vectorized = [p.split("/")[-1] for p in processor.vectorized_files]
    assert "substantive.md" in vectorized
    assert "heading_only.md" not in vectorized


@pytest.mark.asyncio
async def test_all_nonsubstantive_dir_not_vectorized(monkeypatch):
    root_uri = "viking://session/test-session"
    tree = {
        root_uri: [
            {"name": "a.md", "isDir": False},
            {"name": "b.md", "isDir": False},
        ],
    }
    processor, fake_fs, executor = _run_executor(monkeypatch, tree, root_uri)
    await executor.run(root_uri)

    assert processor.vectorized_dirs == []  # neutral overview not embedded
    written = dict(fake_fs.writes)
    assert written[f"{root_uri}/.overview.md"] == _neutral_directory_overview("test-session")


# --------------------------------------------------------------------------- #
# Transition cleanup — substantive -> non-substantive must DELETE stale records
# (PR #3049 review), not merely skip re-vectorization.
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rewritten_nonsubstantive_file_deletes_stale_vector(monkeypatch):
    root_uri = "viking://session/test-session"
    tree = {root_uri: [{"name": "sub_notes.md", "isDir": False}]}

    # Pass 1: substantive content gets vectorized, nothing deleted.
    processor, fake_fs, executor = _run_executor(monkeypatch, tree, root_uri)
    await executor.run(root_uri)
    assert len(processor.vectorized_files) == 1
    file_uri = processor.vectorized_files[0]
    assert fake_fs.deleted == []

    # Pass 2: same file rewritten to non-substantive. A fresh full run
    # (need_vectorize=True) exercises the same delete branch the incremental
    # change-detection path takes.
    processor.substantive_override["sub_notes.md"] = False
    _, _, executor2 = _run_executor(
        monkeypatch, tree, root_uri, processor=processor, fake_fs=fake_fs
    )
    await executor2.run(root_uri)

    assert file_uri in fake_fs.deleted  # stale DETAIL record removed
    assert processor.vectorized_files == [file_uri]  # no new vectorize scheduled


@pytest.mark.asyncio
async def test_dir_turned_neutral_deletes_stale_records(monkeypatch):
    root_uri = "viking://session/test-session"
    tree = {root_uri: [{"name": "sub_a.md", "isDir": False}]}

    # Pass 1: dir has substantive content -> L0/L1 vectorized, nothing deleted.
    processor, fake_fs, executor = _run_executor(monkeypatch, tree, root_uri)
    await executor.run(root_uri)
    assert processor.vectorized_dirs == [root_uri]
    assert root_uri not in fake_fs.deleted

    # Pass 2: only child rewritten non-substantive -> neutral overview.
    processor.substantive_override["sub_a.md"] = False
    _, _, executor2 = _run_executor(
        monkeypatch, tree, root_uri, processor=processor, fake_fs=fake_fs
    )
    await executor2.run(root_uri)

    assert root_uri in fake_fs.deleted  # stale L0/L1 records removed
    assert processor.vectorized_dirs == [root_uri]  # neutral overview not re-embedded


@pytest.mark.asyncio
async def test_write_failure_suppression_does_not_delete(monkeypatch):
    # A failed sidecar write suppresses vectorization but the content is still
    # substantive — existing vectors must survive.
    root_uri = "viking://session/test-session"
    tree = {root_uri: [{"name": "sub_a.md", "isDir": False}]}
    processor, fake_fs, executor = _run_executor(monkeypatch, tree, root_uri)
    monkeypatch.setattr(
        "openviking.storage.queuefs.semantic_dag.write_semantic_sidecars",
        AsyncMock(return_value=False),
    )
    await executor.run(root_uri)

    assert processor.vectorized_dirs == []  # suppressed by write failure
    assert fake_fs.deleted == []  # ...but nothing deleted


# --------------------------------------------------------------------------- #
# Memory path (_process_memory_directory) — same transition cleanup wiring
# --------------------------------------------------------------------------- #
class _MemoryFakeVikingFS(_FakeVikingFS):
    async def read_file(self, path, ctx=None):
        raise FileNotFoundError(path)


async def _run_memory_processor(monkeypatch, file_names):
    _mock_transaction_layer(monkeypatch)
    monkeypatch.setattr(sp, "get_openviking_config", lambda: _fake_config(MagicMock()))
    dir_uri = "viking://user/memories"
    tree = {dir_uri: [{"name": n, "isDir": False} for n in file_names]}
    fake_fs = _MemoryFakeVikingFS(tree)
    monkeypatch.setattr(sp, "get_viking_fs", lambda: fake_fs)

    processor = SemanticProcessor()
    gate = _GateFakeProcessor()
    monkeypatch.setattr(
        processor, "_generate_single_file_summary", gate._generate_single_file_summary
    )

    async def _fake_overview(dir_uri, file_summaries, children_abstracts, llm_sem=None):
        return await gate._generate_overview(dir_uri, file_summaries, children_abstracts)

    monkeypatch.setattr(processor, "_generate_overview", _fake_overview)
    monkeypatch.setattr(processor, "_vectorize_single_file", AsyncMock())
    monkeypatch.setattr(processor, "_vectorize_directory", AsyncMock())

    from openviking.storage.queuefs.semantic_msg import SemanticMsg

    msg = SemanticMsg(uri=dir_uri, context_type="memory")
    ctx = RequestContext(user=UserIdentifier("acc1", "user1"), role=Role.USER)
    await processor._process_memory_directory(msg, ctx=ctx)
    return processor, fake_fs, dir_uri


@pytest.mark.asyncio
async def test_memory_path_deletes_stale_file_vector(monkeypatch):
    processor, fake_fs, dir_uri = await _run_memory_processor(
        monkeypatch, ["sub_a.md", "heading_only.md"]
    )

    assert fake_fs.deleted == [f"{dir_uri}/heading_only.md"]  # stale DETAIL removed
    processor._vectorize_directory.assert_awaited_once()  # dir still substantive


@pytest.mark.asyncio
async def test_memory_path_deletes_stale_dir_records_when_neutral(monkeypatch):
    processor, fake_fs, dir_uri = await _run_memory_processor(
        monkeypatch, ["heading_only.md", "frontmatter.md"]
    )

    assert f"{dir_uri}/heading_only.md" in fake_fs.deleted
    assert f"{dir_uri}/frontmatter.md" in fake_fs.deleted
    assert dir_uri in fake_fs.deleted  # neutral overview -> L0/L1 removed
    processor._vectorize_directory.assert_not_awaited()


if __name__ == "__main__":
    pytest.main([__file__])
