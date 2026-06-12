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

    def test_instruction_explains_peer_memory_routing(self):
        provider = SessionExtractContextProvider(messages=[])

        instruction = provider.instruction()

        assert "Peer Memory" in instruction
        assert "profile/preferences/entities/events" in instruction
        assert "cases/patterns/tools/skills" in instruction


class TestSessionConversationToolFiltering:
    def test_session_conversation_omits_skill_tool_call(self):
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
                        skill_uri="viking://user/skills/create_presentation",
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

        assert "ToolCall:" not in conversation
        assert "create_presentation" not in conversation
        assert "Running a skill." in conversation

    def test_session_conversation_omits_regular_tool_call(self):
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

        assert "ToolCall:" not in conversation
        assert "tool_name=read" not in conversation
        assert "Running a tool." in conversation

    def test_agent_provider_conversation_includes_tool_call_evidence(self):
        from openviking.session.memory.agent_trajectory_context_provider import (
            AgentTrajectoryContextProvider,
        )

        messages = [
            Message(
                id="m1",
                role="assistant",
                parts=[
                    TextPart("Checking the reservation."),
                    ToolPart(
                        tool_id="tool_1",
                        tool_name="get_reservation_details",
                        tool_input={"reservation_id": "EHGLP3"},
                        tool_output="available",
                        tool_status="completed",
                    ),
                ],
            )
        ]
        provider = AgentTrajectoryContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "ToolCall: tool_name=get_reservation_details" in conversation
        assert "input={'reservation_id': 'EHGLP3'}" in conversation
        assert "output=available" in conversation

    def test_assemble_conversation_uses_peer_id_when_present(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[TextPart("My invoice is still missing.")],
                peer_id="web:visitor:alice",
            )
        ]
        provider = SessionExtractContextProvider(messages=messages)

        conversation = provider._assemble_conversation(messages)

        assert "[0][user][web:visitor:alice]" in conversation
        assert "[0][user][default]" not in conversation

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

    def test_detect_language_prefers_user_text_over_assistant_text(self):
        messages = [
            Message(
                id="m1",
                role="user",
                parts=[TextPart("请把记忆保持为中文，继续优化。")],
            ),
            Message(
                id="m2",
                role="assistant",
                parts=[TextPart("한국어 응답이 섞였습니다")],
            ),
        ]

        provider = SessionExtractContextProvider(messages=messages)

        assert provider._detect_language() == "zh-CN"
