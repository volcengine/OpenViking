"""Tests for ContinueDevWatcher parsing and normalization logic."""
import time
from typing import Dict, Optional

from openviking.daemon.watchers.continue_dev_watcher import ContinueDevWatcher


class FakeCursorManager:
    """Minimal stub for testing."""
    def __init__(self):
        self.cursors = {}
        self.updates = []

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
        self.updates.append((file_path, position))


def _make_watcher(tmp_path, batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = ContinueDevWatcher(
        watch_dir=str(tmp_path),
        cursor_manager=cursor_mgr,
        batch_callback=lambda events: batches.append(events),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


def test_parse_line_valid_json(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    line = '{"role": "user", "content": "Hello"}'
    result = w.parse_line(line)
    assert result is not None
    assert result["role"] == "user"
    assert result["content"] == "Hello"


def test_parse_line_invalid_json(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.parse_line("not valid json") is None
    assert w.parse_line("") is None
    assert w.parse_line("   ") is None


def test_parse_line_non_dict(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.parse_line('"just a string"') is None
    assert w.parse_line("[1, 2, 3]") is None
    assert w.parse_line("42") is None


def test_normalize_standard_format(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "user", "content": "How do I sort a list?"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "How do I sort a list?"
    assert event["type"] == "message"


def test_normalize_assistant(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "assistant", "content": "Use the sorted() function."}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "assistant"
    assert event["content"] == "Use the sorted() function."


def test_normalize_missing_content_filtered(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    # Empty content
    raw = {"role": "user", "content": ""}
    assert w.normalize_event(raw) is None

    # Missing content key entirely
    raw = {"role": "user"}
    assert w.normalize_event(raw) is None


def test_normalize_invalid_role_filtered(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "system", "content": "System message"}
    assert w.normalize_event(raw) is None

    raw = {"role": "tool", "content": "Tool output"}
    assert w.normalize_event(raw) is None

    raw = {"content": "No role at all"}
    assert w.normalize_event(raw) is None


def test_normalize_session_id_mapping(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    # sessionId field
    raw = {"role": "user", "content": "test", "sessionId": "session-abc-123"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["session_id"] == "session-abc-123"

    # session_id field (alternative)
    raw = {"role": "user", "content": "test", "session_id": "session-xyz-789"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["session_id"] == "session-xyz-789"


def test_normalize_workspace_directory_to_project_name(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {
        "role": "user",
        "content": "test",
        "workspaceDirectory": "/home/user/my-project",
    }
    event = w.normalize_event(raw)
    assert event is not None
    assert event["project_name"] == "/home/user/my-project"


def test_normalize_timestamp(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "user", "content": "test", "timestamp": "2024-01-15T10:30:00Z"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["timestamp"] == "2024-01-15T10:30:00Z"


def test_normalize_optional_fields_none(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "user", "content": "minimal event"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["timestamp"] is None
    assert event["session_id"] is None
    assert event["project_name"] is None


def test_tool_name(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.tool_name == "continue_dev"


def test_process_file_integration(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=2)

    test_file = tmp_path / "continue.json"
    test_file.write_text(
        '{"role": "user", "content": "Hello"}\n'
        '{"role": "assistant", "content": "Hi there"}\n',
        encoding="utf-8",
    )

    w._process_file(str(test_file))

    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["tool_name"] == "continue_dev"
    assert batches[0][1]["role"] == "assistant"


def test_process_file_filters_non_chat(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=100)

    test_file = tmp_path / "continue.json"
    test_file.write_text(
        '{"role": "user", "content": "Hello"}\n'
        '{"role": "system", "content": "System prompt"}\n'
        '{"role": "assistant", "content": "Hi"}\n'
        'not json at all\n',
        encoding="utf-8",
    )

    w._process_file(str(test_file))
    w.flush()

    assert len(batches) == 1
    assert len(batches[0]) == 2  # only user + assistant, system filtered
    assert batches[0][0]["role"] == "user"
    assert batches[0][1]["role"] == "assistant"
