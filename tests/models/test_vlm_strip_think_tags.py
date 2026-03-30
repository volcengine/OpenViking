# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for stripping <think> reasoning tags from VLM responses."""

import pytest

from openviking.models.vlm.base import _THINK_TAG_RE, VLMBase


class TestStripThinkTags:
    """Test _clean_response strips <think> blocks correctly."""

    @pytest.fixture()
    def vlm(self):
        """Create a minimal concrete VLMBase for testing."""

        class _Stub(VLMBase):
            def get_completion(self, prompt, thinking=False):
                return ""

            async def get_completion_async(self, prompt, thinking=False, max_retries=0):
                return ""

            def get_vision_completion(self, prompt, images, thinking=False):
                return ""

            async def get_vision_completion_async(self, prompt, images, thinking=False):
                return ""

        return _Stub({"api_key": "test"})

    def test_no_think_tags(self, vlm):
        text = "This is a normal response."
        assert vlm._clean_response(text) == "This is a normal response."

    def test_single_think_block(self, vlm):
        text = "<think>\nI need to analyze this.\n</think>\nThe actual summary."
        assert vlm._clean_response(text) == "The actual summary."

    def test_think_block_at_end(self, vlm):
        text = "Summary text.\n<think>some reasoning</think>"
        assert vlm._clean_response(text) == "Summary text."

    def test_think_block_in_middle(self, vlm):
        text = "Start.<think>reasoning here</think>End."
        assert vlm._clean_response(text) == "Start.End."

    def test_multiple_think_blocks(self, vlm):
        text = "<think>first</think>Hello<think>second</think> world"
        assert vlm._clean_response(text) == "Hello world"

    def test_multiline_think_block(self, vlm):
        text = (
            "<think>\nStep 1: analyze the document\n"
            "Step 2: summarize\nStep 3: output\n</think>\n"
            "# Directory Overview\n\nThis directory contains..."
        )
        result = vlm._clean_response(text)
        assert result.startswith("# Directory Overview")
        assert "<think>" not in result

    def test_empty_string(self, vlm):
        assert vlm._clean_response("") == ""

    def test_only_think_block(self, vlm):
        text = "<think>all reasoning, no output</think>"
        assert vlm._clean_response(text) == ""

    def test_nested_angle_brackets_preserved(self, vlm):
        text = "Use <b>bold</b> and <i>italic</i> formatting."
        assert vlm._clean_response(text) == text

    def test_json_with_think_prefix(self, vlm):
        text = '<think>let me think</think>\n{"abstract": "summary", "overview": "details"}'
        result = vlm._clean_response(text)
        assert result == '{"abstract": "summary", "overview": "details"}'


class TestThinkTagRegex:
    """Test the compiled regex pattern directly."""

    def test_greedy_minimal(self):
        """Ensure non-greedy matching: each <think>...</think> is matched individually."""
        text = "<think>a</think>KEEP<think>b</think>"
        assert _THINK_TAG_RE.sub("", text) == "KEEP"
