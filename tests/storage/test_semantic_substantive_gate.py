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
    monkeypatch.setattr(
        "openviking.session.memory.utils.language.resolve_output_language", lambda *a, **k: "en"
    )

    captured = {}

    async def _fake_single(dir_uri, file_summaries_str, children_abstracts_str, file_index_map, **k):
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
    monkeypatch.setattr(
        "openviking.storage.transaction.get_lock_manager", lambda: MagicMock()
    )


class _FakeVikingFS:
    def __init__(self, tree):
        self._tree = tree
        self.writes = []

    async def ls(self, uri, node_limit=None, ctx=None):
        return self._tree.get(uri, [])

    async def write_file(self, path, content, ctx=None, lock_handle=None):
        self.writes.append((path, content))

    def _uri_to_path(self, uri, ctx=None):
        return uri.replace("viking://", "/local/acc1/")


class _GateFakeProcessor:
    """Returns a substantive/non-substantive summary per file name and a neutral
    overview when nothing substantive remains — exercises the DAG gates."""

    def __init__(self):
        self.vectorized_files = []
        self.vectorized_dirs = []

    async def _generate_single_file_summary(self, file_path, llm_sem=None, ctx=None):
        name = file_path.split("/")[-1]
        substantive = "sub" in name
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

    async def _vectorize_directory(self, uri, context_type, abstract, overview, ctx=None,
                                   semantic_msg_id=None):
        self.vectorized_dirs.append(uri)

    async def _vectorize_single_file(self, parent_uri, context_type, file_path, summary_dict,
                                     ctx=None, semantic_msg_id=None, use_summary=False):
        self.vectorized_files.append(file_path)


class _DummyTracker:
    async def register(self, **_kwargs):
        return None


def _run_executor(monkeypatch, tree, root_uri):
    _mock_transaction_layer(monkeypatch)
    fake_fs = _FakeVikingFS(tree)
    monkeypatch.setattr("openviking.storage.queuefs.semantic_dag.get_viking_fs", lambda: fake_fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.embedding_tracker.EmbeddingTaskTracker.get_instance",
        lambda: _DummyTracker(),
    )
    processor = _GateFakeProcessor()
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


if __name__ == "__main__":
    pytest.main([__file__])
