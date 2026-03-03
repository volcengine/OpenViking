"""Tests for agent context building."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

from vikingbot.agent.context import ContextBuilder


class TestContextBuilder:
    """Tests for ContextBuilder."""

    @pytest.fixture
    def context_builder(self):
        """Create a context builder instance."""
        return ContextBuilder(workspace_dir=Path("/tmp/test_workspace"))

    def test_initialization(self, context_builder):
        """Test context builder initialization."""
        assert context_builder.workspace_dir == Path("/tmp/test_workspace")
        assert context_builder.system_prompt is None
        assert context_builder.memory_content is None
        assert context_builder.history_content is None

    @patch("builtins.open", new_callable=mock_open, read_data="# System Prompt\nYou are a helpful assistant.")
    @patch("pathlib.Path.exists", return_value=True)
    def test_load_agents_md(self, mock_exists, mock_file, context_builder):
        """Test loading AGENTS.md file."""
        context_builder._load_system_prompt()

        assert "You are a helpful assistant" in str(context_builder.system_prompt)

    @patch("builtins.open", new_callable=mock_open, read_data="# Memory\n- User likes Python\n- User works at ACME")
    @patch("pathlib.Path.exists", return_value=True)
    def test_load_memory_md(self, mock_exists, mock_file, context_builder):
        """Test loading MEMORY.md file."""
        context_builder._load_memory()

        assert "User likes Python" in str(context_builder.memory_content)
        assert "User works at ACME" in str(context_builder.memory_content)

    @patch("builtins.open", new_callable=mock_open, read_data="# History\n- 2024-01-01: User asked about Python\n- 2024-01-02: User asked about testing")
    @patch("pathlib.Path.exists", return_value=True)
    def test_load_history_md(self, mock_exists, mock_file, context_builder):
        """Test loading HISTORY.md file."""
        context_builder._load_history()

        assert "2024-01-01" in str(context_builder.history_content)

    @patch.object(ContextBuilder, '_load_system_prompt')
    @patch.object(ContextBuilder, '_load_memory')
    @patch.object(ContextBuilder, '_load_history')
    def test_build_context(self, mock_history, mock_memory, mock_system, context_builder):
        """Test building complete context."""
        # Set up mock returns
        context_builder.system_prompt = "System: Be helpful"
        context_builder.memory_content = "Memory: User likes Python"
        context_builder.history_content = "History: Previous conversation"

        context = context_builder.build()

        assert "System: Be helpful" in context
        assert "Memory: User likes Python" in context
        assert "History: Previous conversation" in context

    def test_build_context_truncation(self, context_builder):
        """Test context truncation when too long."""
        # Create a very long context
        context_builder.system_prompt = "System: " + "x" * 10000
        context_builder.memory_content = "Memory: " + "y" * 10000
        context_builder.history_content = "History: " + "z" * 10000

        context = context_builder.build(max_tokens=1000)

        # Context should be truncated
        assert len(context) < 30000  # Much less than the raw concatenation


class TestContextBuilderEdgeCases:
    """Edge case tests for ContextBuilder."""

    def test_missing_files(self):
        """Test behavior when files don't exist."""
        builder = ContextBuilder(workspace_dir=Path("/nonexistent"))

        # Should not raise errors
        builder._load_system_prompt()
        builder._load_memory()
        builder._load_history()

        # All content should be None
        assert builder.system_prompt is None
        assert builder.memory_content is None
        assert builder.history_content is None

    def test_empty_files(self, tmp_path):
        """Test behavior with empty files."""
        # Create empty files
        (tmp_path / "AGENTS.md").write_text("")
        (tmp_path / "MEMORY.md").write_text("")
        (tmp_path / "HISTORY.md").write_text("")

        builder = ContextBuilder(workspace_dir=tmp_path)
        builder._load_system_prompt()
        builder._load_memory()
        builder._load_history()

        # Should handle empty files gracefully
        assert builder.system_prompt is not None
        assert builder.memory_content is not None
        assert builder.history_content is not None
