# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking.storage.semantic_sidecar import (
    parse_semantic_sidecar,
    render_semantic_sidecar,
)


def _patch_semantic_limits(monkeypatch, *, abstract_max_chars=256, overview_max_chars=4000):
    config = SimpleNamespace(
        semantic=SimpleNamespace(
            abstract_max_chars=abstract_max_chars,
            overview_max_chars=overview_max_chars,
        )
    )
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)


class _ParentRefreshFS:
    def __init__(self, files, events):
        self.files = dict(files)
        self.events = events
        self._async_agfs = self

    def _uri_to_path(self, uri, ctx=None):
        return uri

    async def pathlock_acquire_exact_batch(self, paths):
        return {"paths": paths}

    async def pathlock_release(self, lease):
        return None

    async def read_file(self, uri, ctx=None):
        return self.files[uri]

    async def write_file(self, uri, content, ctx=None, lease_ref=None):
        self.files[uri] = content
        self.events.append(("write", uri))


class _ParentRefreshQueue:
    def __init__(self, events, error=None):
        self.events = events
        self.error = error
        self.messages = []

    async def enqueue(self, msg):
        self.events.append(("enqueue", msg.uri))
        if self.error is not None:
            raise self.error
        self.messages.append(msg)
        return "msg-1"


class _ParentRefreshQueueManager:
    SEMANTIC = "semantic"

    def __init__(self, queue):
        self.queue = queue

    def get_queue(self, name, allow_create=False):
        assert name == self.SEMANTIC
        assert allow_create is True
        return self.queue


def _parent_sidecars(parent_uri):
    metadata = {
        "freshness": {
            "total_entries": 1,
            "sampled_entries": 1,
            "unsampled_entries": 0,
            "pending_child_changes": 0,
        }
    }
    return {
        f"{parent_uri}/.abstract.md": render_semantic_sidecar(
            ContextLevel.ABSTRACT, parent_uri, "Parent abstract.", metadata
        ),
        f"{parent_uri}/.overview.md": render_semantic_sidecar(
            ContextLevel.OVERVIEW, parent_uri, "# Parent overview", metadata
        ),
    }


@pytest.mark.asyncio
async def test_parent_refresh_marks_sidecars_pending_before_enqueue(monkeypatch):
    parent_uri = "viking://resources/project"
    child_uri = f"{parent_uri}/child"
    events = []
    fs = _ParentRefreshFS(_parent_sidecars(parent_uri), events)
    before = {uri: parse_semantic_sidecar(raw) for uri, raw in fs.files.items()}
    queue = _ParentRefreshQueue(events)
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _ParentRefreshQueueManager(queue),
    )

    await SemanticProcessor()._enqueue_parent_refresh(
        SemanticMsg(uri=child_uri, context_type="resource"), child_uri
    )

    assert events[-1] == ("enqueue", parent_uri)
    assert [event[0] for event in events[:-1]] == ["write", "write"]
    for uri, raw in fs.files.items():
        after = parse_semantic_sidecar(raw)
        assert after.body == before[uri].body
        assert after.metadata["freshness"]["pending_child_changes"] == 1
    parent_msg = queue.messages[0]
    assert parent_msg.changes == {"modified": [child_uri]}
    assert parent_msg.generation_trigger == "parent_refresh"


@pytest.mark.asyncio
async def test_parent_refresh_keeps_pending_when_enqueue_fails(monkeypatch):
    parent_uri = "viking://resources/project"
    child_uri = f"{parent_uri}/child"
    events = []
    fs = _ParentRefreshFS(_parent_sidecars(parent_uri), events)
    queue = _ParentRefreshQueue(events, error=RuntimeError("queue unavailable"))
    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: fs)
    monkeypatch.setattr(
        "openviking.storage.queuefs.get_queue_manager",
        lambda: _ParentRefreshQueueManager(queue),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        await SemanticProcessor()._enqueue_parent_refresh(
            SemanticMsg(uri=child_uri, context_type="resource"), child_uri
        )

    for raw in fs.files.values():
        assert parse_semantic_sidecar(raw).metadata["freshness"]["pending_child_changes"] == 1


def test_markdown_overview_uses_brief_description_as_abstract(monkeypatch):
    _patch_semantic_limits(monkeypatch)
    processor = SemanticProcessor()
    generated = (
        "# README\n\n"
        "This brief description is the retrieval abstract.\n\n"
        "## Quick Navigation\n\n"
        "- Read README.md"
    )

    overview, abstract = processor._normalize_overview_generation(generated)

    assert overview == generated
    assert abstract == "This brief description is the retrieval abstract."


def test_markdown_overview_extracts_multiline_brief_description(monkeypatch):
    _patch_semantic_limits(monkeypatch)
    processor = SemanticProcessor()
    generated = (
        "# README\n\n"
        "This is the first abstract line.\n"
        "This is the second abstract line.\n\n"
        "## Quick Navigation\n\n"
        "- Read README.md"
    )

    overview, abstract = processor._normalize_overview_generation(generated)

    assert overview == generated
    assert abstract == "This is the first abstract line.\nThis is the second abstract line."


def test_okf_overview_frontmatter_is_not_part_of_l0_or_size_limit(monkeypatch):
    _patch_semantic_limits(monkeypatch, overview_max_chars=80)
    processor = SemanticProcessor()
    body = "# README\n\nVisible brief.\n\n## Navigation\n\n- README.md"
    raw = render_semantic_sidecar(
        ContextLevel.OVERVIEW,
        "viking://resources/demo",
        body,
        {
            "source": {"kind": "http", "uri": "https://example.com/very-long-source"},
            "generated_by": {"component": "SemanticProcessor", "trigger": "test"},
            "freshness": {
                "total_entries": 1,
                "sampled_entries": 1,
                "unsampled_entries": 0,
                "pending_child_changes": 0,
            },
        },
    )

    overview, abstract = processor._normalize_overview_generation(raw)

    assert overview == body
    assert abstract == "Visible brief."
    assert "generated_by" not in overview


def test_body_truncation_preserves_okf_metadata_when_rendered(monkeypatch):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=32, overview_max_chars=64)
    processor = SemanticProcessor()
    metadata = {
        "source": {"kind": "http", "uri": "https://example.com/source.md"},
        "generated_by": {"component": "SemanticProcessor", "trigger": "ingest"},
        "freshness": {
            "total_entries": 3,
            "sampled_entries": 2,
            "unsampled_entries": 1,
            "pending_child_changes": 0,
        },
    }
    raw = render_semantic_sidecar(
        ContextLevel.OVERVIEW,
        "viking://resources/demo",
        "# Demo\n\nA compact sentence. " + ("Long navigation detail. " * 10),
        metadata,
    )
    original = parse_semantic_sidecar(raw)

    overview, abstract = processor._normalize_overview_generation(raw)
    rewritten = parse_semantic_sidecar(
        render_semantic_sidecar(
            ContextLevel.OVERVIEW,
            "viking://resources/demo",
            overview,
            original.metadata,
        )
    )

    assert len(rewritten.body.rstrip()) <= 64
    assert len(abstract) <= 32
    assert rewritten.metadata == original.metadata


def test_index_references_are_replaced_inside_markdown_overview(monkeypatch):
    _patch_semantic_limits(monkeypatch)
    processor = SemanticProcessor()
    generated = "# README\n\nUse [1] to get started."

    replaced = processor._replace_index_references(generated, {1: "README.md"})

    assert replaced == "# README\n\nUse README.md to get started."


def test_abstract_truncation_prefers_complete_sentence(monkeypatch):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=80)
    processor = SemanticProcessor()
    abstract = (
        "This is a complete sentence. "
        "This second sentence contains onboarding material that would be cut."
    )

    overview, abstract = processor._enforce_size_limits("# README\n\nBody", abstract)

    assert overview == "# README\n\nBody"
    assert abstract == "This is a complete sentence."


def test_abstract_truncation_keeps_first_sentence_even_over_limit(monkeypatch):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=80)
    processor = SemanticProcessor()
    first_sentence = (
        "This directory is a timestamped media storage container for a single MP4 video "
        "file, organized to preserve the exact capture or creation time of its contents."
    )
    abstract = f"{first_sentence} This second sentence should be omitted."

    _, abstract = processor._enforce_size_limits("# video\n\nBody", abstract)

    assert abstract == first_sentence


def test_overview_truncation_prefers_complete_sentence(monkeypatch):
    _patch_semantic_limits(monkeypatch, overview_max_chars=45)
    processor = SemanticProcessor()
    overview = (
        "# README\n\nThis is a complete sentence. This second sentence would be cut in the middle."
    )

    overview, abstract = processor._enforce_size_limits(overview, "abstract")

    assert overview == "# README\n\nThis is a complete sentence."
    assert abstract == "abstract"


def test_overview_truncation_keeps_last_complete_sentence_within_limit(monkeypatch):
    _patch_semantic_limits(monkeypatch, overview_max_chars=57)
    processor = SemanticProcessor()
    overview = "# README\n\nFirst sentence. Second sentence. Third sentence should be omitted."

    overview, abstract = processor._enforce_size_limits(overview, "abstract")

    assert overview == "# README\n\nFirst sentence. Second sentence."
    assert abstract == "abstract"


def test_truncation_keeps_multiple_short_sentences_within_limit(monkeypatch):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=10)
    processor = SemanticProcessor()

    _, abstract = processor._enforce_size_limits("# README\n\nBody", "A. B. C. D.E.")

    assert abstract == "A. B. C."


def test_abstract_truncation_does_not_treat_decimal_point_as_sentence_end_without_period(
    monkeypatch,
):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=24)
    processor = SemanticProcessor()
    abstract = "This covers version 3.14 compatibility checks for onboarding"

    _, abstract = processor._enforce_size_limits("# README\n\nBody", abstract)

    assert abstract == "This covers version..."


def test_abstract_truncation_accepts_sentence_period_after_number(monkeypatch):
    _patch_semantic_limits(monkeypatch, abstract_max_chars=70)
    processor = SemanticProcessor()
    abstract = (
        "This import check was generated at 16:55. "
        "This second sentence would otherwise be truncated midstream."
    )

    _, abstract = processor._enforce_size_limits("# README\n\nBody", abstract)

    assert abstract == "This import check was generated at 16:55."
