"""
Integration tests for multi-watcher daemon pipeline.
Tests the full flow: multiple watchers -> normalized events -> ETL compatibility.
"""
import os
import time
import pytest
from pathlib import Path

from openviking.daemon.watchers.registry import create_watcher, list_available_watchers
from openviking.daemon.watchers import BaseWatcher


class FakeCursorManager:
    """In-memory cursor manager for testing."""

    def __init__(self):
        self.cursors = {}

    def get_cursor(self, file_path):
        from openviking.daemon.models import FileCursor
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        from openviking.daemon.models import FileCursor
        self.cursors[file_path] = FileCursor(
            file_path=file_path,
            last_position=position,
            last_read_time=time.time(),
        )


# --- Registry Tests ---

def test_all_watchers_registered():
    """All 5 built-in watchers should be registered."""
    available = list_available_watchers()
    expected = {"claude_code", "generic_jsonl", "aider", "cursor", "continue_dev"}
    assert expected.issubset(set(available)), f"Missing: {expected - set(available)}"


def test_create_all_watchers():
    """Verify all registered watchers can be instantiated via factory."""
    batches = []
    cm = FakeCursorManager()

    file_patterns = {
        "claude_code": "*.jsonl",
        "generic_jsonl": "*.jsonl",
        "aider": ".aider.chat.history.md",
        "cursor": "*.log",
        "continue_dev": "*.json",
    }

    for tool_name in list_available_watchers():
        watcher = create_watcher(
            tool_name=tool_name,
            watch_dir="/tmp/test",
            cursor_manager=cm,
            batch_callback=lambda e: batches.append(e),
            file_pattern=file_patterns.get(tool_name, "*.jsonl"),
        )
        assert isinstance(watcher, BaseWatcher)
        assert watcher.tool_name == tool_name


def test_create_unknown_watcher_raises():
    """Unknown tool name should raise ValueError."""
    with pytest.raises(ValueError, match="Unknown watcher tool"):
        create_watcher(
            tool_name="nonexistent",
            watch_dir="/tmp",
            cursor_manager=FakeCursorManager(),
            batch_callback=lambda e: None,
        )


# --- Multi-Watcher Normalization Tests ---

def test_claude_code_events_have_tool_name(tmp_path):
    """Claude Code events should include tool_name='claude_code'."""
    batches = []
    cm = FakeCursorManager()
    watcher = create_watcher(
        tool_name="claude_code",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=2,
    )

    test_file = tmp_path / "session.jsonl"
    test_file.write_text(
        '{"type": "user", "message": {"role": "user", "content": "Hello from CC"}}\n'
        '{"type": "assistant", "message": {"role": "assistant", "content": "Hi from CC"}}\n'
    )
    watcher._process_file(str(test_file))

    assert len(batches) == 1
    assert all(e["tool_name"] == "claude_code" for e in batches[0])


def test_generic_jsonl_custom_mapping(tmp_path):
    """GenericJSONL with custom field mapping should normalize correctly."""
    batches = []
    cm = FakeCursorManager()
    watcher = create_watcher(
        tool_name="generic_jsonl",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=1,
        extra={
            "role_field": "author",
            "user_role_value": "human",
            "assistant_role_value": "ai",
            "content_field": "text",
        },
    )

    test_file = tmp_path / "custom.jsonl"
    test_file.write_text('{"author": "human", "text": "Custom format test"}\n')
    watcher._process_file(str(test_file))

    assert len(batches) == 1
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["content"] == "Custom format test"
    assert batches[0][0]["tool_name"] == "generic_jsonl"


def test_aider_multiline_parsing(tmp_path):
    """Aider markdown format should produce user+assistant events."""
    batches = []
    cm = FakeCursorManager()
    watcher = create_watcher(
        tool_name="aider",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        batch_trigger_lines=100,
    )

    test_file = tmp_path / ".aider.chat.history.md"
    test_file.write_text(
        "# aider chat started at 2026-01-15 10:30:00\n\n"
        "> /my/project\n\n"
        "#### user:\n"
        "How do I use fastapi?\n\n"
        "#### assistant:\n"
        "FastAPI is great for building APIs.\n"
    )
    watcher._process_file(str(test_file))
    watcher.flush()

    assert len(batches) == 1
    events = batches[0]
    assert len(events) == 2
    assert events[0]["role"] == "user"
    assert events[0]["content"] == "How do I use fastapi?"
    assert events[0]["tool_name"] == "aider"
    assert events[1]["role"] == "assistant"


def test_cursor_log_parsing(tmp_path):
    """Cursor log format should be normalized correctly."""
    batches = []
    cm = FakeCursorManager()
    watcher = create_watcher(
        tool_name="cursor",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        file_pattern="*.log",
        batch_trigger_lines=1,
    )

    test_file = tmp_path / "cursor.log"
    test_file.write_text(
        '{"type": "chat", "role": "user", "message": "Fix this bug"}\n'
    )
    watcher._process_file(str(test_file))

    assert len(batches) == 1
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["content"] == "Fix this bug"
    assert batches[0][0]["tool_name"] == "cursor"


def test_continue_dev_log_parsing(tmp_path):
    """Continue.dev JSON format should be normalized."""
    batches = []
    cm = FakeCursorManager()
    watcher = create_watcher(
        tool_name="continue_dev",
        watch_dir=str(tmp_path),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        file_pattern="*.json",
        batch_trigger_lines=1,
    )

    test_file = tmp_path / "continue.json"
    test_file.write_text(
        '{"role": "user", "content": "Explain this code", "sessionId": "abc123"}\n'
    )
    watcher._process_file(str(test_file))

    assert len(batches) == 1
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["tool_name"] == "continue_dev"
    assert batches[0][0]["session_id"] == "abc123"


# --- Cross-Watcher ETL Compatibility ---

def test_normalized_events_compatible_with_reconstructor():
    """Events from all watchers should work with ConversationReconstructor."""
    from openviking.daemon.conversation_reconstructor import ConversationReconstructor

    events = [
        {"role": "user", "content": "Question from CC", "tool_name": "claude_code",
         "timestamp": "2026-01-15T10:00:00Z", "session_id": "s1", "project_name": "proj"},
        {"role": "assistant", "content": "Answer from CC", "tool_name": "claude_code",
         "timestamp": "2026-01-15T10:00:01Z", "session_id": "s1", "project_name": "proj"},
        {"role": "user", "content": "Question from Aider", "tool_name": "aider",
         "timestamp": "2026-01-15T10:00:02Z"},
        {"role": "assistant", "content": "Answer from Aider", "tool_name": "aider",
         "timestamp": "2026-01-15T10:00:03Z"},
    ]

    reconstructor = ConversationReconstructor()
    turns = reconstructor.reconstruct(events)

    assert len(turns) == 2
    assert turns[0].user_prompt == "Question from CC"
    assert turns[1].user_prompt == "Question from Aider"


def test_normalized_events_compatible_with_filter():
    """Events from all watchers should work with LowValueFilter."""
    from openviking.daemon.filters import LowValueFilter

    events = [
        {"role": "user", "content": "A meaningful question about architecture",
         "tool_name": "claude_code"},
        {"role": "user", "content": "npm install express",
         "tool_name": "cursor"},
        {"role": "assistant", "content": "Here is a detailed explanation of the design pattern",
         "tool_name": "aider"},
    ]

    f = LowValueFilter()
    filtered = f.apply(events)

    assert len(filtered) == 2
    assert all("npm install" not in e["content"] for e in filtered)


def test_source_tool_propagated_through_pipeline():
    """source_tool should flow from events through ConversationTurn."""
    from openviking.daemon.conversation_reconstructor import ConversationReconstructor

    events = [
        {"role": "user", "content": "How to use FastAPI?", "tool_name": "cursor",
         "timestamp": "2026-01-15T10:00:00Z"},
        {"role": "assistant", "content": "Install FastAPI with pip...", "tool_name": "cursor",
         "timestamp": "2026-01-15T10:00:01Z"},
    ]

    reconstructor = ConversationReconstructor()
    turns = reconstructor.reconstruct(events)

    assert len(turns) == 1
    assert turns[0].source_tool == "cursor"


def test_multi_watcher_config_effective_watchers():
    """DaemonConfig.get_effective_watchers() should handle all cases."""
    from openviking.server.config import WatcherConfig, DaemonConfig

    # Explicit watchers list
    cfg = DaemonConfig(
        enabled=True,
        watchers=[
            WatcherConfig(tool_name="claude_code", watch_dir="/a"),
            WatcherConfig(tool_name="aider", watch_dir="/b"),
            WatcherConfig(tool_name="cursor", watch_dir="/c", enabled=False),
        ],
    )
    effective = cfg.get_effective_watchers()
    assert len(effective) == 2  # disabled watcher filtered out
    assert effective[0].tool_name == "claude_code"
    assert effective[1].tool_name == "aider"

    # Backward compat: watch_dir only
    cfg2 = DaemonConfig(enabled=True, watch_dir="~/.claude/projects")
    effective2 = cfg2.get_effective_watchers()
    assert len(effective2) == 1
    assert effective2[0].tool_name == "claude_code"

    # Default fallback
    cfg3 = DaemonConfig(enabled=True)
    effective3 = cfg3.get_effective_watchers()
    assert len(effective3) == 1
    assert effective3[0].tool_name == "claude_code"


def test_knowledge_router_uses_source_tool():
    """KnowledgeRouter should use source_tool in URI path."""
    from openviking.daemon.knowledge_router import KnowledgeRouter
    from openviking.daemon.models import ExtractedKnowledge

    router = KnowledgeRouter()

    # With source_tool
    k1 = ExtractedKnowledge(
        status="EXTRACTED", category="skills", title="FastAPI Tips",
        content="...", confidence=0.9, source_tool="cursor",
    )
    uri1 = router.route(k1)
    assert "cursor" in uri1
    assert "claude_code" not in uri1

    # Without source_tool (fallback)
    k2 = ExtractedKnowledge(
        status="EXTRACTED", category="skills", title="Python Tips",
        content="...", confidence=0.9,
    )
    uri2 = router.route(k2)
    assert "general" in uri2
