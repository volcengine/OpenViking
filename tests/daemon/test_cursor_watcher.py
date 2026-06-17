"""Tests for CursorWatcher parsing and normalization logic."""
import time
from typing import Dict, Optional

from openviking.daemon.watchers.cursor_watcher import CursorWatcher


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
    watcher = CursorWatcher(
        watch_dir=str(tmp_path),
        cursor_manager=cursor_mgr,
        batch_callback=lambda events: batches.append(events),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


def test_parse_line_valid_json(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    line = '{"type": "chat", "role": "user", "message": "Hello"}'
    result = w.parse_line(line)
    assert result is not None
    assert result["role"] == "user"
    assert result["message"] == "Hello"


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


def test_normalize_standard_chat_format(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"type": "chat", "role": "user", "message": "How do I sort a list?"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "How do I sort a list?"
    assert event["type"] == "message"


def test_normalize_human_role(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "human", "content": "What is Python?"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "What is Python?"


def test_normalize_human_turn_role(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "human_turn", "text": "Explain recursion"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "user"
    assert event["content"] == "Explain recursion"


def test_normalize_ai_response(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "ai_response", "text": "Recursion is when a function calls itself."}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "assistant"
    assert event["content"] == "Recursion is when a function calls itself."


def test_normalize_assistant_role(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "assistant", "message": "Here is the answer."}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "assistant"
    assert event["content"] == "Here is the answer."


def test_normalize_ai_role(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "ai", "content": "AI generated response"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "assistant"
    assert event["content"] == "AI generated response"


def test_normalize_bot_role(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "bot", "message": "Bot reply"}
    event = w.normalize_event(raw)
    assert event is not None
    assert event["role"] == "assistant"
    assert event["content"] == "Bot reply"


def test_normalize_non_chat_event_filtered(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    # System message should be filtered
    raw = {"role": "system", "content": "System prompt"}
    assert w.normalize_event(raw) is None

    # No role
    raw = {"type": "info", "content": "some log"}
    assert w.normalize_event(raw) is None

    # Empty content
    raw = {"role": "user", "message": ""}
    assert w.normalize_event(raw) is None


def test_normalize_timestamp_fields(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "user", "message": "test", "timestamp": "2024-01-15T10:30:00Z"}
    event = w.normalize_event(raw)
    assert event["timestamp"] == "2024-01-15T10:30:00Z"

    # Also check ts field
    raw = {"role": "user", "message": "test", "ts": "2024-01-15T11:00:00Z"}
    event = w.normalize_event(raw)
    assert event["timestamp"] == "2024-01-15T11:00:00Z"


def test_normalize_session_id_mapping(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    raw = {"role": "user", "message": "test", "conversationId": "conv-123"}
    event = w.normalize_event(raw)
    assert event["session_id"] == "conv-123"

    raw = {"role": "user", "message": "test", "session_id": "sess-456"}
    event = w.normalize_event(raw)
    assert event["session_id"] == "sess-456"


def test_tool_name(tmp_path):
    w, _, _ = _make_watcher(tmp_path)
    assert w.tool_name == "cursor"


def test_process_file_integration(tmp_path):
    w, batches, _ = _make_watcher(tmp_path, batch_trigger_lines=2)

    test_file = tmp_path / "cursor.log"
    test_file.write_text(
        '{"role": "user", "message": "Hello"}\n'
        '{"role": "assistant", "message": "Hi there"}\n',
        encoding="utf-8",
    )

    w._process_file(str(test_file))

    assert len(batches) == 1
    assert len(batches[0]) == 2
    assert batches[0][0]["role"] == "user"
    assert batches[0][0]["tool_name"] == "cursor"
    assert batches[0][1]["role"] == "assistant"
