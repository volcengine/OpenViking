# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test that provider instruction correctly instructs LLM.
"""

from openviking.message import Message, TextPart, ToolPart
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider


class TestProviderInstruction:
    """Test the provider instruction contains correct instructions."""

    def test_instruction_contains_read_before_edit_instructions(self):
        """Test that instruction explicitly tells LLM to read files before editing."""
        # Create provider with mock messages
        mock_messages = []
        provider = SessionExtractContextProvider(messages=mock_messages)

        instruction = provider.instruction()

        # Check for critical instructions
        assert (
            "Before editing ANY existing memory file, you MUST first read its complete content"
            in instruction
        )
        assert (
            "ONLY read URIs that are explicitly listed in ls tool results or returned by previous tool calls"
            in instruction
        )

    def test_instruction_contains_output_language(self):
        """Test that instruction includes the output language setting."""
        mock_messages = []
        provider = SessionExtractContextProvider(messages=mock_messages)

        instruction = provider.instruction()

        # Check that output language instruction is present
        assert "Target Output Language" in instruction
        assert "All memory content MUST be written in" in instruction


class TestSkillToolCallExposure:
    def test_assemble_conversation_includes_skill_tool_call(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[
                    TextPart("Running a skill."),
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        skill_uri="viking://agent/skills/create_presentation",
                        tool_input={"file_path": "/skills/ppt/SKILL.md"},
                        tool_output="ok",
                        tool_status="completed",
                        duration_ms=123,
                    ),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[ToolCall]" in conversation
        assert '"skill_name": "create_presentation"' in conversation

    def test_assemble_conversation_without_skill_tool_call_has_no_skill_name(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[
                    TextPart("Running a tool."),
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        tool_input={"file_path": "README.md"},
                        tool_output="ok",
                        tool_status="completed",
                        duration_ms=123,
                    ),
                ],
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[ToolCall]" in conversation
        assert '"tool_name": "read"' in conversation
        assert '"skill_name":' not in conversation

    def test_detect_language_only_uses_text_parts(self):
        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[TextPart("Please keep the memory in English.")],
            ),
            Message(
                id="m2",
                role="assistant",
                parts=[
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="read",
                        tool_uri="viking://session/test/tools/tool_1",
                        tool_input={"file_path": "README.md"},
                        tool_output="这是中文工具输出",
                        tool_status="completed",
                    )
                ],
            ),
        ]

        provider = SessionExtractContextProvider(messages=messages)

        assert provider._detect_language() == "en"
