# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

import pytest

from openviking.core.context import ContextLevel
from openviking.storage.abstract_overview import render_abstract_overview
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _patch_semantic_limits(monkeypatch, *, abstract_max_chars=256, overview_max_chars=4000):
    config = SimpleNamespace(
        semantic=SimpleNamespace(
            abstract_max_chars=abstract_max_chars,
            overview_max_chars=overview_max_chars,
        )
    )
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)


async def _completed(value):
    return value


async def _raise(error):
    raise error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_type"),
    [
        (LockAcquisitionError("sidecars busy"), LockAcquisitionError),
        (ValueError("write failed"), RuntimeError),
    ],
)
async def test_memory_directory_write_error_contract(monkeypatch, error, expected_type):
    # _handle_message relies on this exact type to requeue lock conflicts without
    # incrementing the API circuit breaker or dropping the semantic message.
    processor = SemanticProcessor()
    msg = SimpleNamespace(
        uri="viking://resources/memory",
        changes=None,
        skip_vectorization=True,
        source=None,
        generation_trigger="semantic_refresh",
    )

    class FakeVikingFS:
        async def ls(self, uri, node_limit, ctx):
            return [{"name": "entry.md", "isDir": False}]

    monkeypatch.setattr(semantic_processor_module, "get_viking_fs", lambda: FakeVikingFS())
    monkeypatch.setattr(
        semantic_processor_module,
        "get_openviking_config",
        lambda: SimpleNamespace(
            semantic=SimpleNamespace(
                abstract_max_chars=256,
                overview_max_chars=4000,
                overview_sample_limit=32,
            )
        ),
    )
    monkeypatch.setattr(
        processor,
        "_generate_single_file_summary",
        lambda *args, **kwargs: _completed({"name": "entry.md", "summary": "summary"}),
    )
    monkeypatch.setattr(
        processor,
        "_generate_overview",
        lambda *args, **kwargs: _completed("# memory\n\nSummary."),
    )
    monkeypatch.setattr(
        semantic_processor_module,
        "write_abstract_overview",
        lambda **kwargs: _raise(error),
    )

    with pytest.raises(expected_type) as exc_info:
        await processor._process_memory_directory(msg)
    if expected_type is RuntimeError:
        assert isinstance(exc_info.value.__cause__, ValueError)


def test_normalize_overview_extracts_brief_without_frontmatter_or_coverage(monkeypatch):
    _patch_semantic_limits(monkeypatch)
    processor = SemanticProcessor()
    brief = "This is the first abstract line.\nThis is the second abstract line."
    generated = (
        f"# README\n\n---\n\n{brief}\n\n"
        "## Directory Coverage\n\n"
        "This directory contains 513 direct entries; 32 were sampled.\n\n"
        "## Quick Navigation\n\n"
        "- Read README.md"
    )
    raw = render_abstract_overview(
        ContextLevel.OVERVIEW,
        "viking://resources/demo",
        generated,
        {"source": {"kind": "http", "uri": "https://example.com/private.pdf"}},
    )

    assert processor._normalize_overview_generation(generated) == (generated, brief)
    assert processor._normalize_overview_generation(raw) == (generated, brief)


def test_parse_overview_keeps_plain_heading_cache_key(monkeypatch):
    _patch_semantic_limits(monkeypatch)
    processor = SemanticProcessor()
    overview = "# docs\n\n## Detailed Description\n\n### README.md\nLegacy summary.\n"

    assert processor._parse_overview_md(overview) == {"README.md": "Legacy summary."}


def test_overview_and_abstract_keep_last_complete_sentence_within_their_limits(monkeypatch):
    _patch_semantic_limits(monkeypatch, overview_max_chars=57, abstract_max_chars=10)
    processor = SemanticProcessor()

    overview, abstract = processor._enforce_size_limits(
        "# README\n\nFirst sentence. Second sentence. Third sentence should be omitted.",
        "A. B. C. D.E.",
    )

    assert overview == "# README\n\nFirst sentence. Second sentence."
    assert abstract == "A. B. C."


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
