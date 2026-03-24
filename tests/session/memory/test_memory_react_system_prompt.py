# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Test that MemoryReAct system prompt correctly instructs LLM to read before edit.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from openviking.session.memory import MemoryReAct


class TestMemoryReActSystemPrompt:
    """Test the system prompt contains correct instructions about reading before edit."""

    @pytest.fixture
    def mock_viking_fs(self):
        """Mock VikingFS."""
        mock = MagicMock()
        mock.read_file = AsyncMock(return_value="")
        mock.write_file = AsyncMock()
        mock.ls = AsyncMock(return_value=[])
        mock.mkdir = AsyncMock()
        mock.rm = AsyncMock()
        mock.stat = AsyncMock(return_value={"type": "dir"})
        mock.find = AsyncMock(return_value={"memories": [], "resources": [], "skills": []})
        mock.tree = AsyncMock(return_value={"uri": "", "tree": []})
        return mock

    @patch('openviking.session.memory.memory_react.get_viking_fs')
    def test_system_prompt_contains_read_before_edit_instructions(self, mock_get_viking_fs, mock_viking_fs):
        """Test that system prompt explicitly tells LLM to read files before editing."""
        mock_get_viking_fs.return_value = mock_viking_fs

        # Create MemoryReAct with mock dependencies
        mock_llm = MagicMock()
        mock_llm.get_default_model.return_value = "test-model"

        react = MemoryReAct(llm_provider=mock_llm, viking_fs=mock_viking_fs)

        # Get system prompt
        system_prompt = react._get_system_prompt("zh")

        # Check for critical instructions
        assert "Critical: Read Before Edit" in system_prompt
        assert "Before you edit or update ANY existing memory file, you MUST first use the read tool" in system_prompt
        assert "The ls tool only shows you what files exist - it does NOT show you the file content" in system_prompt
        assert "You MUST use the read tool to get the actual content of any file you want to edit" in system_prompt
        assert "Without reading the actual file first, your edit operations will fail" in system_prompt

    @patch('openviking.session.memory.memory_react.get_viking_fs')
    def test_system_prompt_contains_note_in_important_notes(self, mock_get_viking_fs, mock_viking_fs):
        """Test that important notes section also reminds to read before edit."""
        mock_get_viking_fs.return_value = mock_viking_fs

        mock_llm = MagicMock()
        mock_llm.get_default_model.return_value = "test-model"

        react = MemoryReAct(llm_provider=mock_llm, viking_fs=mock_viking_fs)
        system_prompt = react._get_system_prompt("zh")

        assert "Always read a file before editing it - ls and summaries are not enough" in system_prompt

    @patch('openviking.session.memory.memory_react.get_viking_fs')
    def test_ls_result_has_note_about_reading_files(self, mock_get_viking_fs, mock_viking_fs):
        """Test that pre-fetched ls results include a note about needing to read files."""
        mock_get_viking_fs.return_value = mock_viking_fs

        mock_llm = MagicMock()
        mock_llm.get_default_model.return_value = "test-model"

        react = MemoryReAct(llm_provider=mock_llm, viking_fs=mock_viking_fs)

        # Test with a pre-fetched context that has directories
        pre_fetched = {
            "directories": {
                "viking://test/memories": [
                    {"name": "test.md", "isDir": False}
                ]
            },
            "summaries": {},
            "search_results": []
        }

        messages = react._format_pre_fetched_as_tool_calls(pre_fetched)

        # Check that we have messages (ls call + result, find call + result)
        assert len(messages) >= 2

        # Find the ls tool result message
        ls_result_msg = None
        for msg in messages:
            if msg.get("role") == "tool" and "prefetch_ls" in msg.get("tool_call_id", ""):
                ls_result_msg = msg
                break

        assert ls_result_msg is not None, "Could not find ls tool result message"

        # The tool result message should contain our note
        content = json.loads(ls_result_msg["content"])
        assert "_note" in content
        assert "This ls result only shows file names. Use read tool to get actual file content before editing any file." in content["_note"]
