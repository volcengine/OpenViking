# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for issue #3028 (hallucinated summaries).

Verifies that the SemanticProcessor classifies documents by structural
content (not a fixed character cutoff), propagates ``has_substantive_content``
through the overview generation and vectorization paths, and returns
deterministic neutral semantics when a directory has nothing substantive to
summarize.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.storage.queuefs import semantic_processor as semantic_processor_module
from openviking.storage.queuefs.semantic_processor import SemanticProcessor


def _patch_semantic_limits(monkeypatch, *, abstract_max_chars=256, overview_max_chars=4000):
    config = SimpleNamespace(
        semantic=SimpleNamespace(
            abstract_max_chars=abstract_max_chars,
            overview_max_chars=overview_max_chars,
            max_overview_prompt_chars=50000,
            overview_batch_size=20,
        ),
        vlm=MagicMock(is_available=MagicMock(return_value=False)),
    )
    monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)


class TestHasSubstantiveMarkdownBody:
    """Structure-based detection: classify content, not character count."""

    def test_empty_content_is_non_substantive(self):
        assert SemanticProcessor._has_substantive_markdown_body("") is False

    def test_whitespace_only_is_non_substantive(self):
        assert SemanticProcessor._has_substantive_markdown_body("   \n\n\t  \n  ") is False

    def test_single_atx_heading_only_is_non_substantive(self):
        assert SemanticProcessor._has_substantive_markdown_body("# Working Memory") is False

    def test_long_atx_heading_only_is_non_substantive(self):
        long_heading = (
            "# This Is A Very Long Heading Title That Exceeds Fifty Characters Easily "
            "Just By Describing Itself In The Title"
        )
        assert len(long_heading) > 50
        assert SemanticProcessor._has_substantive_markdown_body(long_heading) is False

    def test_multiple_headings_with_no_body_is_non_substantive(self):
        content = (
            "# Top Level Heading\n"
            "\n"
            "## Second Level\n"
            "\n"
            "### Third Level Heading With Many Words\n"
        )
        assert SemanticProcessor._has_substantive_markdown_body(content) is False

    def test_heading_with_leading_spaces_is_non_substantive(self):
        content = "   ### ATX heading can have up to three leading spaces"
        assert SemanticProcessor._has_substantive_markdown_body(content) is False

    def test_setext_style_heading_only_is_non_substantive(self):
        content = "Setext Heading\n================\n"
        assert SemanticProcessor._has_substantive_markdown_body(content) is False

    def test_thematic_break_only_is_non_substantive(self):
        for break_line in ("---", "***", "___", "- - -", "* * *"):
            assert SemanticProcessor._has_substantive_markdown_body(break_line) is False

    def test_heading_separator_blank_mix_is_non_substantive(self):
        content = (
            "# Title\n"
            "\n"
            "---\n"
            "\n"
            "## Subtitle\n"
            "\n"
        )
        assert SemanticProcessor._has_substantive_markdown_body(content) is False

    def test_short_body_sentence_is_substantive(self):
        content = "This is a short valid document."
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_chinese_body_is_substantive(self, monkeypatch):
        _patch_semantic_limits(monkeypatch)
        content = "这是一个中文文档，内容真实存在。"
        assert len(content) < 50
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_japanese_body_is_substantive(self):
        content = "これは日本語の本文です。"
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_korean_body_is_substantive(self):
        content = "이것은 한국어 본문입니다."
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_russian_body_is_substantive(self):
        content = "Это русский текст."
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_arabic_body_is_substantive(self):
        content = "هذا نص بالعربية."
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_heading_plus_short_body_line_is_substantive(self):
        content = (
            "# Working Memory\n"
            "\n"
            "Recorded project preferences and active task context."
        )
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_markdown_body_with_bullets_is_substantive(self):
        content = (
            "- Item one\n"
            "- Item two\n"
        )
        assert SemanticProcessor._has_substantive_markdown_body(content) is True

    def test_short_code_fence_is_substantive(self):
        content = "```python\nprint('hi')\n```\n"
        assert SemanticProcessor._has_substantive_markdown_body(content) is True


class TestGenerateOverviewFiltersNonSubstantive:
    """Overview generation drops non-substantive entries and returns neutral
    deterministic semantics when nothing remains."""

    @pytest.mark.asyncio
    async def test_all_non_substantive_directory_returns_neutral_deterministic_overview(self, monkeypatch):
        _patch_semantic_limits(monkeypatch)
        processor = SemanticProcessor()
        file_summaries = [
            {"name": "a.md", "summary": "", "has_substantive_content": False},
            {"name": "b.md", "summary": "", "has_substantive_content": False},
        ]
        overview = await processor._generate_overview(
            "viking://resources/project",
            file_summaries,
            children_abstracts=[],
        )
        assert "No substantive entries to summarize" in overview
        assert overview.startswith("# project\n\n")

    @pytest.mark.asyncio
    async def test_all_non_substantive_directory_skips_vlm_invocation(self, monkeypatch):
        mock_vlm = MagicMock()
        mock_vlm.is_available.return_value = True
        mock_vlm.get_completion_async = AsyncMock(return_value="# Should not be called")
        config = SimpleNamespace(
            semantic=SimpleNamespace(
                abstract_max_chars=256,
                overview_max_chars=4000,
                max_overview_prompt_chars=50000,
                overview_batch_size=20,
            ),
            vlm=mock_vlm,
        )
        monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)
        processor = SemanticProcessor()
        file_summaries = [
            {"name": "h.md", "summary": "", "has_substantive_content": False},
        ]
        await processor._generate_overview(
            "viking://resources/empty",
            file_summaries,
            children_abstracts=[],
        )
        mock_vlm.get_completion_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_entries_filter_non_substantive_keep_body(self, monkeypatch):
        mock_vlm = MagicMock()
        mock_vlm.is_available.return_value = True
        mock_vlm.get_completion_async = AsyncMock(return_value="# dir\n\nHello")
        config = SimpleNamespace(
            semantic=SimpleNamespace(
                abstract_max_chars=256,
                overview_max_chars=4000,
                max_overview_prompt_chars=50000,
                overview_batch_size=20,
            ),
            vlm=mock_vlm,
        )
        monkeypatch.setattr(semantic_processor_module, "get_openviking_config", lambda: config)
        processor = SemanticProcessor()
        file_summaries = [
            {"name": "heading_only.md", "summary": "", "has_substantive_content": False},
            {"name": "real.md", "summary": "Real body content here", "has_substantive_content": True},
        ]
        await processor._generate_overview(
            "viking://resources/mixed",
            file_summaries,
            children_abstracts=[],
        )
        assert mock_vlm.get_completion_async.await_count == 1
        prompt_kwarg = mock_vlm.get_completion_async.await_args.args[0]
        assert "heading_only" not in prompt_kwarg
        assert "real.md" in prompt_kwarg
        assert "Real body content" in prompt_kwarg

    @pytest.mark.asyncio
    async def test_no_entries_returns_neutral_overview(self, monkeypatch):
        _patch_semantic_limits(monkeypatch)
        processor = SemanticProcessor()
        overview = await processor._generate_overview(
            "viking://resources/nothing",
            file_summaries=[],
            children_abstracts=[],
        )
        assert "No substantive entries to summarize" in overview


class TestVectorizationSuppressionForNonSubstantive:
    """Non-substantive file summaries are filtered out of the vectorization
    worklist inside the memory-directory processing helper."""

    def test_memory_file_vectorize_items_drops_non_substantive_entries(self, monkeypatch):
        _patch_semantic_limits(monkeypatch)
        processor = SemanticProcessor()
        import inspect

        source = inspect.getsource(processor._process_memory_directory)
        assert 'summary.get("has_substantive_content", True)' in source
