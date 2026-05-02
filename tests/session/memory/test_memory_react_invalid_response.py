# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Tests for ExtractLoop's recovery from unparseable LLM responses.

Reproduces issue #1541: when the model returns plain prose instead of the
expected JSON, the loop must persist the failure context so the next
iteration can self-correct rather than repeating the same drift.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openviking.session.memory.extract_loop import ExtractLoop


@pytest.fixture
def mock_vlm():
    vlm = MagicMock()
    vlm.model = "test-model"
    vlm.max_retries = 2
    vlm.get_completion_async = AsyncMock()
    return vlm


@pytest.fixture
def mock_viking_fs():
    return MagicMock()


@pytest.fixture
def extract_loop(mock_vlm, mock_viking_fs):
    """Build an ExtractLoop with the JSON schema field already populated.

    The helper under test reads ``self._json_schema`` directly, which is
    normally set inside ``run()``. We patch a representative schema in here
    so we can exercise the helper in isolation.
    """
    with (
        patch("openviking.session.memory.extract_loop.SchemaModelGenerator"),
        patch("openviking.session.memory.extract_loop.SchemaPromptGenerator"),
    ):
        loop = ExtractLoop(mock_vlm, mock_viking_fs)
        loop._json_schema = {
            "type": "object",
            "properties": {
                "delete_uris": {"type": "array", "items": {"type": "string"}},
                "preferences": {"type": "array"},
            },
            "required": ["delete_uris", "preferences"],
        }
        return loop


class TestAppendInvalidResponseCorrection:
    """Direct tests on the helper that recovers from non-schema responses."""

    def test_appends_assistant_and_user_messages(self, extract_loop):
        messages = [{"role": "system", "content": "irrelevant"}]
        extract_loop._append_invalid_response_correction(
            messages,
            content="I'd be happy to help! Sure, here are the memories...",
            error="Expected dict after parsing, got <class 'str'>",
        )

        assert len(messages) == 3
        assistant_msg, correction_msg = messages[1], messages[2]

        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"] == ("I'd be happy to help! Sure, here are the memories...")

        assert correction_msg["role"] == "user"
        assert "could not be parsed" in correction_msg["content"]
        assert "Expected dict" in correction_msg["content"]

    def test_correction_includes_json_schema(self, extract_loop):
        messages = []
        extract_loop._append_invalid_response_correction(
            messages, content="oops not JSON", error="parse error"
        )
        correction_body = messages[1]["content"]
        # Schema must travel with the correction so the model has a fresh
        # reminder of what shape to emit.
        assert "delete_uris" in correction_body
        assert "preferences" in correction_body
        assert "```json" in correction_body

    def test_correction_explicitly_forbids_prose_and_markdown_fences(self, extract_loop):
        messages = []
        extract_loop._append_invalid_response_correction(messages, content="x", error="e")
        correction_body = messages[1]["content"]
        assert "ONLY a JSON object" in correction_body
        assert "no prose" in correction_body
        assert "no markdown fences" in correction_body

    def test_truncates_oversized_content(self, extract_loop):
        # Models occasionally generate multi-KB ramblings; we don't want to
        # blow up the next prompt with a copy of the whole thing.
        big = "x" * (ExtractLoop._MAX_FAILED_CONTENT_CHARS + 5000)
        messages = []
        extract_loop._append_invalid_response_correction(messages, content=big, error="e")
        assistant_content = messages[0]["content"]
        assert len(assistant_content) < len(big)
        assert assistant_content.endswith("...[truncated]")
        assert len(assistant_content) == ExtractLoop._MAX_FAILED_CONTENT_CHARS + len(
            "...[truncated]"
        )

    def test_short_content_not_truncated(self, extract_loop):
        short = "short reply"
        messages = []
        extract_loop._append_invalid_response_correction(messages, content=short, error="e")
        assert messages[0]["content"] == short

    def test_handles_empty_content(self, extract_loop):
        # In practice content may be ``None`` (no choices) or an empty string;
        # both should produce a usable correction message instead of crashing.
        messages = []
        extract_loop._append_invalid_response_correction(messages, content="", error="e")
        assert messages[0]["content"] == ""
        assert messages[1]["role"] == "user"

        messages2 = []
        extract_loop._append_invalid_response_correction(messages2, content=None, error="e")
        assert messages2[0]["content"] == ""

    def test_schema_in_correction_is_valid_json(self, extract_loop):
        # The schema is embedded inside a fenced code block — make sure the
        # extracted JSON between the fences round-trips cleanly so the model
        # sees a parseable schema reference.
        messages = []
        extract_loop._append_invalid_response_correction(messages, content="x", error="e")
        body = messages[1]["content"]
        start = body.index("```json\n") + len("```json\n")
        end = body.index("\n```", start)
        embedded = body[start:end]
        round_tripped = json.loads(embedded)
        assert round_tripped == extract_loop._json_schema


class TestCallLLMPersistsFailureContext:
    """Integration test on _call_llm: parse failure mutates ``messages``
    so the next iteration sees the failure (without it, #1541 reproduces)."""

    @pytest.mark.asyncio
    async def test_unparseable_response_appends_failure_context(self, extract_loop):
        # VLM returns plain prose; parse_json_with_stability returns the bare
        # string and Layer 3 reports "Expected dict ... got <class 'str'>"
        # — the exact symptom from the bug report.
        response = MagicMock()
        response.has_tool_calls = False
        response.tool_calls = []
        response.content = "I'm sorry, I cannot help with this request."
        response.usage = {}
        extract_loop.vlm.get_completion_async = AsyncMock(return_value=response)

        # Pre-populate the precomputed expected_fields/operations_model that
        # _call_llm uses, so the call doesn't depend on full run() setup.
        extract_loop._operations_model = MagicMock()
        extract_loop._expected_fields = ["delete_uris", "preferences"]
        extract_loop._tool_schemas = []
        extract_loop._disable_tools_for_iteration = True

        messages = [{"role": "system", "content": "system prompt"}]
        tool_calls, operations = await extract_loop._call_llm(messages)

        assert tool_calls is None
        assert operations is None
        # Must persist failure context so iteration N+1 can self-correct
        assert len(messages) == 3
        assert messages[1]["role"] == "assistant"
        assert "cannot help" in messages[1]["content"]
        assert messages[2]["role"] == "user"
        assert "could not be parsed" in messages[2]["content"]
