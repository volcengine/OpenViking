# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

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
        "# README\n\n"
        "This is a complete sentence. "
        "This second sentence would be cut in the middle."
    )

    overview, abstract = processor._enforce_size_limits(overview, "abstract")

    assert overview == "# README\n\nThis is a complete sentence."
    assert abstract == "abstract"


def test_overview_truncation_keeps_last_complete_sentence_within_limit(monkeypatch):
    _patch_semantic_limits(monkeypatch, overview_max_chars=57)
    processor = SemanticProcessor()
    overview = (
        "# README\n\n"
        "First sentence. "
        "Second sentence. "
        "Third sentence should be omitted."
    )

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
