# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Test that ExtractLoop system prompt correctly instructs LLM.
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from openviking.session.memory import ExtractLoop


class TestExtractLoopSystemPrompt:
    """Test the system prompt contains correct instructions."""

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

    @patch('openviking.session.memory.extract_loop.get_viking_fs')
    def test_system_prompt_contains_read_before_edit_instructions(self, mock_get_viking_fs, mock_viking_fs):
        """Test that system prompt explicitly tells LLM to read files before editing."""
        mock_get_viking_fs.return_value = mock_viking_fs

        # Create ExtractLoop with mock dependencies
        mock_vlm = MagicMock()
        mock_vlm.model = "test-model"
        mock_vlm.max_retries = 2
        mock_vlm.get_completion_async = AsyncMock()

        extract_loop = ExtractLoop(vlm=mock_vlm, viking_fs=mock_viking_fs)

        # Get system prompt
        system_prompt = extract_loop._get_system_prompt("zh")

        # Check for critical instructions
        assert "Before editing ANY existing memory file, you MUST first read its complete content" in system_prompt
        assert "ONLY read URIs that are explicitly listed in ls tool results or returned by previous tool calls" in system_prompt

    @patch('openviking.session.memory.extract_loop.get_viking_fs')
    def test_system_prompt_contains_output_language(self, mock_get_viking_fs, mock_viking_fs):
        """Test that system prompt includes the output language setting."""
        mock_get_viking_fs.return_value = mock_viking_fs

        mock_vlm = MagicMock()
        mock_vlm.model = "test-model"
        mock_vlm.max_retries = 2
        mock_vlm.get_completion_async = AsyncMock()

        # Test Chinese
        extract_loop = ExtractLoop(vlm=mock_vlm, viking_fs=mock_viking_fs)
        system_prompt = extract_loop._get_system_prompt("zh")
        assert "zh" in system_prompt or "Chinese" in system_prompt

        # Test English
        system_prompt_en = extract_loop._get_system_prompt("en")
        assert "en" in system_prompt_en or "English" in system_prompt_en