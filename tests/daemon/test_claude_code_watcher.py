"""Tests for ClaudeCodeWatcher parsing and filtering logic.

All test fixtures use the REAL Claude Code JSONL format:
- Top-level "type": "user" | "assistant" | ...
- "role" and "content" nested inside "message" object
- "sessionId" at top level (camelCase)
- "content" can be string or array of content blocks
"""
import json
import os
import tempfile

from openviking.daemon.watchers.claude_code_watcher import (
    ClaudeCodeWatcher,
    _extract_text_from_content,
)


class FakeCursorManager:
    """Minimal stub for testing."""
    def __init__(self):
        self.cursors = {}
        self.updates = []

    def get_cursor(self, file_path):
        from openviking.daemon.models import FileCursor
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        self.updates.append((file_path, position))


def _make_watcher(batch_trigger_lines=50, batch_trigger_seconds=300):
    batches = []
    cursor_mgr = FakeCursorManager()
    watcher = ClaudeCodeWatcher(
        watch_dir=tempfile.gettempdir(),
        cursor_manager=cursor_mgr,
        batch_callback=lambda lines: batches.append(lines),
        batch_trigger_lines=batch_trigger_lines,
        batch_trigger_seconds=batch_trigger_seconds,
    )
    return watcher, batches, cursor_mgr


# --- Fixtures matching REAL Claude Code JSONL format ---

def _make_user_event(content="Hello", session_id="test-session-001"):
    """Create a realistic user event matching real Claude Code logs."""
    return {
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": "user-uuid-001",
        "timestamp": "2026-06-15T10:30:00.000Z",
        "sessionId": session_id,
    }


def _make_assistant_event(content="AI answer", session_id="test-session-001"):
    """Create a realistic assistant event with string content."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": content,
        },
        "uuid": "asst-uuid-001",
        "timestamp": "2026-06-15T10:30:01.000Z",
        "sessionId": session_id,
    }


def _make_assistant_event_blocks(blocks, session_id="test-session-001"):
    """Create an assistant event with array content blocks."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": blocks,
        },
        "uuid": "asst-uuid-002",
        "timestamp": "2026-06-15T10:30:02.000Z",
        "sessionId": session_id,
    }


def _make_queue_event():
    """Create a queue-operation event (should be skipped)."""
    return {
        "type": "queue-operation",
        "operation": "enqueue",
        "timestamp": "2026-06-15T10:29:59.000Z",
        "sessionId": "test-session-001",
        "content": "Some prompt",
    }


def _make_system_event():
    """Create a system event (should be skipped)."""
    return {
        "type": "system",
        "subtype": "stop_hook_summary",
        "uuid": "sys-uuid-001",
        "timestamp": "2026-06-15T10:30:03.000Z",
        "sessionId": "test-session-001",
    }


def _make_attachment_event():
    """Create an attachment event (should be skipped)."""
    return {
        "type": "attachment",
        "attachment": {"type": "hook_success"},
        "uuid": "att-uuid-001",
        "timestamp": "2026-06-15T10:29:58.000Z",
        "sessionId": "test-session-001",
    }


def _make_tool_result_user_event():
    """Create a user event carrying tool_result (should be skipped)."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_001",
                    "content": "output here",
                    "is_error": False,
                }
            ],
        },
        "uuid": "tr-uuid-001",
        "timestamp": "2026-06-15T10:30:04.000Z",
        "sessionId": "test-session-001",
    }


# --- Tests ---

def test_tool_name():
    watcher, _, _ = _make_watcher()
    assert watcher.tool_name == "claude_code"


# --- _extract_text_from_content helper ---

def test_extract_text_from_string():
    assert _extract_text_from_content("hello world") == "hello world"


def test_extract_text_from_array_with_text():
    blocks = [
        {"type": "thinking", "thinking": "let me think..."},
        {"type": "text", "text": "Here is the answer."},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ]
    assert _extract_text_from_content(blocks) == "Here is the answer."


def test_extract_text_from_array_multiple_texts():
    blocks = [
        {"type": "text", "text": "Part 1"},
        {"type": "text", "text": "Part 2"},
    ]
    assert _extract_text_from_content(blocks) == "Part 1\nPart 2"


def test_extract_text_from_array_tool_only():
    blocks = [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
    ]
    assert _extract_text_from_content(blocks) == ""


def test_extract_text_from_none():
    assert _extract_text_from_content(None) == ""


def test_extract_text_from_empty_list():
    assert _extract_text_from_content([]) == ""


# --- parse_line ---

def test_parse_valid_jsonl_line():
    watcher, _, _ = _make_watcher()
    event = _make_user_event("Hello")
    line = json.dumps(event)
    result = watcher.parse_line(line)
    assert result is not None
    assert result["type"] == "user"
    assert result["message"]["role"] == "user"


def test_parse_invalid_line():
    watcher, _, _ = _make_watcher()
    assert watcher.parse_line("not valid json") is None
    assert watcher.parse_line("") is None


# --- normalize_event ---

def test_normalize_user_string_message():
    watcher, _, _ = _make_watcher()
    raw = _make_user_event("Hello world")
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "user"
    assert result["content"] == "Hello world"
    assert result["type"] == "message"
    assert result["session_id"] == "test-session-001"


def test_normalize_assistant_string_message():
    watcher, _, _ = _make_watcher()
    raw = _make_assistant_event("AI response here")
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "AI response here"


def test_normalize_assistant_text_block():
    """Assistant with text content block should be extracted."""
    watcher, _, _ = _make_watcher()
    raw = _make_assistant_event_blocks([
        {"type": "thinking", "thinking": "internal reasoning"},
        {"type": "text", "text": "The answer is 42."},
    ])
    result = watcher.normalize_event(raw)
    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "The answer is 42."
    assert "internal reasoning" not in result["content"]


def test_normalize_assistant_tool_use_only_skipped():
    """Assistant message with only tool_use blocks (no text) should be skipped."""
    watcher, _, _ = _make_watcher()
    raw = _make_assistant_event_blocks([
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ])
    result = watcher.normalize_event(raw)
    assert result is None


def test_normalize_tool_result_user_skipped():
    """User events carrying tool_result should be skipped."""
    watcher, _, _ = _make_watcher()
    raw = _make_tool_result_user_event()
    result = watcher.normalize_event(raw)
    assert result is None


def test_normalize_queue_operation_skipped():
    watcher, _, _ = _make_watcher()
    raw = _make_queue_event()
    assert watcher.normalize_event(raw) is None


def test_normalize_system_event_skipped():
    watcher, _, _ = _make_watcher()
    raw = _make_system_event()
    assert watcher.normalize_event(raw) is None


def test_normalize_attachment_event_skipped():
    watcher, _, _ = _make_watcher()
    raw = _make_attachment_event()
    assert watcher.normalize_event(raw) is None


def test_normalize_no_message_field_skipped():
    """Events with type=user/assistant but no message dict should be skipped."""
    watcher, _, _ = _make_watcher()
    assert watcher.normalize_event({"type": "user"}) is None
    assert watcher.normalize_event({"type": "assistant", "message": "not a dict"}) is None


# --- _post_normalize (project_name from path) ---

def test_post_normalize_injects_project_name():
    watcher, _, _ = _make_watcher()
    event = {"role": "user", "content": "test", "project_name": None}
    path = "C:/Users/test/.claude/projects/D--Develop-MyProject/abc123.jsonl"
    result = watcher._post_normalize(event, path)
    assert result["project_name"] == "D--Develop-MyProject"


def test_post_normalize_windows_backslash():
    watcher, _, _ = _make_watcher()
    event = {"role": "user", "content": "test", "project_name": None}
    path = "C:\\Users\\test\\.claude\\projects\\D--Develop-OpenViking\\session.jsonl"
    result = watcher._post_normalize(event, path)
    assert result["project_name"] == "D--Develop-OpenViking"


def test_post_normalize_preserves_existing_project_name():
    watcher, _, _ = _make_watcher()
    event = {"role": "user", "content": "test", "project_name": "already-set"}
    path = "C:/Users/test/.claude/projects/D--Develop-Other/abc.jsonl"
    result = watcher._post_normalize(event, path)
    assert result["project_name"] == "already-set"


# --- filter_event ---

def test_filter_event_keeps_messages():
    watcher, _, _ = _make_watcher()
    event = {"role": "user", "type": "message", "content": "Hello", "tool_name": "claude_code"}
    assert watcher.filter_event(event) is True


# --- _process_file (integration of parse + normalize + buffer) ---

def test_process_file():
    """Test that _process_file reads, parses, normalizes, and buffers events."""
    watcher, batches, cursor_mgr = _make_watcher(batch_trigger_lines=2)

    # Build a realistic session file path
    tmp_dir = tempfile.mkdtemp()
    project_dir = os.path.join(tmp_dir, "projects", "D--Develop-TestProj")
    os.makedirs(project_dir)
    session_file = os.path.join(project_dir, "sess-001.jsonl")

    with open(session_file, "w", encoding="utf-8") as f:
        # queue-operation → should be skipped
        f.write(json.dumps(_make_queue_event()) + "\n")
        # user message → should be extracted
        f.write(json.dumps(_make_user_event("Hello")) + "\n")
        # attachment → should be skipped
        f.write(json.dumps(_make_attachment_event()) + "\n")
        # assistant text reply → should be extracted
        f.write(json.dumps(_make_assistant_event("Hi there")) + "\n")
        # system event → should be skipped
        f.write(json.dumps(_make_system_event()) + "\n")
        # tool_result user → should be skipped
        f.write(json.dumps(_make_tool_result_user_event()) + "\n")

    try:
        watcher._process_file(session_file)
        # batch_trigger_lines=2, so 2 valid events should trigger flush
        assert len(batches) == 1
        assert len(batches[0]) == 2
        assert batches[0][0]["role"] == "user"
        assert batches[0][0]["content"] == "Hello"
        assert batches[0][1]["role"] == "assistant"
        assert batches[0][1]["content"] == "Hi there"
        assert all(e["tool_name"] == "claude_code" for e in batches[0])
        # project_name should be derived from path
        assert batches[0][0]["project_name"] == "D--Develop-TestProj"
        # Cursor should have been updated
        assert len(cursor_mgr.updates) == 1
        assert cursor_mgr.updates[0][0] == session_file
    finally:
        os.unlink(session_file)
        os.rmdir(project_dir)
        os.rmdir(os.path.join(tmp_dir, "projects"))
        os.rmdir(tmp_dir)


def test_force_flush_empty_buffer():
    watcher, batches, _ = _make_watcher()
    watcher.flush()
    assert len(batches) == 0


def test_force_flush_with_data():
    watcher, batches, _ = _make_watcher()
    watcher._buffer.add_line({"role": "user", "content": "test", "tool_name": "claude_code"}, 10)
    watcher.flush()
    assert len(batches) == 1
