# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
Test that provider instruction correctly instructs LLM.
"""

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
