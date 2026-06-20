"""
Unit tests for CursorDBWatcher.
Tests dual-SQLite architecture: global DB (cursorDiskKV) + workspace DB (ItemTable).
Uses temporary SQLite databases to simulate real Cursor storage.
"""
import json
import os
import sqlite3
import time
import pytest

from openviking.daemon.watchers.cursor_db_watcher import CursorDBWatcher
from openviking.daemon.watchers import BaseWatcher
from openviking.daemon.models import FileCursor


class FakeCursorManager:
    """In-memory cursor manager for testing."""

    def __init__(self):
        self.cursors = {}

    def get_cursor(self, file_path):
        return self.cursors.get(file_path, FileCursor(file_path=file_path))

    def update_cursor(self, file_path, position):
        self.cursors[file_path] = FileCursor(
            file_path=file_path,
            last_position=position,
            last_read_time=time.time(),
        )


def _create_global_db(db_path, bubbles):
    """Create a mock global state.vscdb with cursorDiskKV table.

    Args:
        db_path: Path for the SQLite file
        bubbles: List of (key, value_dict) tuples
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cursorDiskKV (
            [key] TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    for key, value in bubbles:
        conn.execute(
            "INSERT INTO cursorDiskKV ([key], value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )
    conn.commit()
    conn.close()


def _create_workspace_db(db_path, composers):
    """Create a mock workspace state.vscdb with ItemTable.

    Args:
        db_path: Path for the SQLite file
        composers: List of composer dicts with 'id' field
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ItemTable (
            [key] TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    composer_data = {"allComposers": composers}
    conn.execute(
        "INSERT INTO ItemTable ([key], value) VALUES (?, ?)",
        ("composer.composerData", json.dumps(composer_data)),
    )
    conn.commit()
    conn.close()


def _make_cursor_user_dir(tmp_path):
    """Create a mock Cursor User directory structure."""
    user_dir = tmp_path / "Cursor" / "User"
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


# --- Protocol ---

def test_implements_base_watcher():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert isinstance(w, BaseWatcher)
    assert w.tool_name == "cursor_db"


# --- resolve_db_path ---

def test_resolve_db_path_found(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)
    global_db = user_dir / "globalStorage" / "state.vscdb"
    _create_global_db(str(global_db), [])

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert w.resolve_db_path() == str(global_db)


def test_resolve_db_path_not_found(tmp_path):
    user_dir = tmp_path / "empty"
    user_dir.mkdir()

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert w.resolve_db_path() is None


# --- query_new_events ---

def test_query_new_events_empty_db(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)
    global_db = user_dir / "globalStorage" / "state.vscdb"
    _create_global_db(str(global_db), [])

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    events = w.query_new_events(0)
    assert events == []


def test_query_new_events_returns_bubbles(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)
    global_db = user_dir / "globalStorage" / "state.vscdb"

    bubbles = [
        ("bubbleId:comp1:bub1", {
            "_v": 3, "type": 1, "text": "Hello Cursor",
            "createdAt": "2026-06-20T10:00:00Z",
        }),
        ("bubbleId:comp1:bub2", {
            "_v": 3, "type": 2, "text": "Hi! How can I help?",
            "createdAt": "2026-06-20T10:00:01Z",
        }),
        ("nonBubbleKey", {"some": "other data"}),
    ]
    _create_global_db(str(global_db), bubbles)

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    events = w.query_new_events(0)

    # Should only return bubbleId:* keys, not nonBubbleKey
    assert len(events) == 2
    assert all(e["key"].startswith("bubbleId:") for e in events)
    assert events[0]["composer_id"] == "comp1"
    assert events[0]["_cursor_position"] > 0


def test_query_new_events_respects_cursor(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)
    global_db = user_dir / "globalStorage" / "state.vscdb"

    bubbles = [
        ("bubbleId:c1:b1", {"_v": 3, "type": 1, "text": "old msg", "createdAt": "t1"}),
        ("bubbleId:c1:b2", {"_v": 3, "type": 2, "text": "new msg", "createdAt": "t2"}),
    ]
    _create_global_db(str(global_db), bubbles)

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    # First query: get all
    all_events = w.query_new_events(0)
    assert len(all_events) == 2

    # Second query: use last rowid as cursor
    last_rowid = all_events[-1]["rowid"]
    new_events = w.query_new_events(last_rowid)
    assert len(new_events) == 0


# --- normalize_event ---

def test_normalize_user_message():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {
        "value": {
            "_v": 3,
            "type": 1,
            "text": "How do I use FastAPI?",
            "createdAt": "2026-06-20T10:00:00Z",
        },
        "composer_id": "comp-abc",
        "_cursor_position": 5,
    }
    result = w.normalize_event(raw)

    assert result is not None
    assert result["role"] == "user"
    assert result["content"] == "How do I use FastAPI?"
    assert result["type"] == "message"
    assert result["timestamp"] == "2026-06-20T10:00:00Z"
    assert result["session_id"] == "comp-abc"


def test_normalize_assistant_message():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {
        "value": {
            "_v": 3,
            "type": 2,
            "text": "FastAPI is a modern Python framework.",
            "createdAt": "2026-06-20T10:00:01Z",
            "allThinkingBlocks": [{"thinking": "Let me think..."}],
        },
        "composer_id": "comp-abc",
        "_cursor_position": 6,
    }
    result = w.normalize_event(raw)

    assert result is not None
    assert result["role"] == "assistant"
    assert result["content"] == "FastAPI is a modern Python framework."


def test_normalize_skips_empty_text():
    """Streaming artifacts with empty text should be skipped."""
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {
        "value": {"_v": 3, "type": 2, "text": "", "createdAt": "t"},
        "composer_id": "c1",
        "_cursor_position": 7,
    }
    assert w.normalize_event(raw) is None


def test_normalize_skips_unknown_type():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {
        "value": {"_v": 3, "type": 99, "text": "unknown", "createdAt": "t"},
        "composer_id": "c1",
        "_cursor_position": 8,
    }
    assert w.normalize_event(raw) is None


def test_normalize_skips_non_dict_value():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {"value": "not a dict", "composer_id": "c1", "_cursor_position": 9}
    assert w.normalize_event(raw) is None


def test_normalize_future_schema_version():
    """Future _v values should produce a debug log, not crash."""
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )

    raw = {
        "value": {"_v": 99, "type": 1, "text": "future format", "createdAt": "t"},
        "composer_id": "c1",
        "_cursor_position": 10,
    }
    result = w.normalize_event(raw)
    assert result is not None  # Still processes with warning


# --- filter_event ---

def test_filter_short_content():
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir="/tmp/fake",
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    assert w.filter_event({"content": "ok"}) is False  # < 10 chars
    assert w.filter_event({"content": "This is long enough content"}) is True


# --- _discover_composer_ids ---

def test_discover_composer_ids(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)

    # Create two workspace DBs
    ws1_db = user_dir / "workspaceStorage" / "hash1" / "state.vscdb"
    _create_workspace_db(str(ws1_db), [
        {"id": "comp-1", "createdAt": "t1"},
        {"id": "comp-2", "createdAt": "t2"},
    ])

    ws2_db = user_dir / "workspaceStorage" / "hash2" / "state.vscdb"
    _create_workspace_db(str(ws2_db), [
        {"id": "comp-3", "createdAt": "t3"},
    ])

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    ids = w._discover_composer_ids()
    assert set(ids) == {"comp-1", "comp-2", "comp-3"}


def test_discover_composer_ids_no_workspaces(tmp_path):
    user_dir = _make_cursor_user_dir(tmp_path)
    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: None,
    )
    ids = w._discover_composer_ids()
    assert ids == []


# --- Integration: full poll cycle ---

def test_full_poll_cycle(tmp_path):
    """End-to-end: create mock DBs -> start watcher -> verify batch_callback."""
    batches = []
    user_dir = _make_cursor_user_dir(tmp_path)
    global_db = user_dir / "globalStorage" / "state.vscdb"

    bubbles = [
        ("bubbleId:comp1:b1", {
            "_v": 3, "type": 1,
            "text": "How do I implement a binary search in Python?",
            "createdAt": "2026-06-20T10:00:00Z",
        }),
        ("bubbleId:comp1:b2", {
            "_v": 3, "type": 2,
            "text": "Here is a binary search implementation using iterative approach...",
            "createdAt": "2026-06-20T10:00:01Z",
        }),
    ]
    _create_global_db(str(global_db), bubbles)

    cm = FakeCursorManager()
    w = CursorDBWatcher(
        watch_dir=str(user_dir),
        cursor_manager=cm,
        batch_callback=lambda e: batches.append(e),
        poll_interval=1,
        batch_trigger_lines=2,
    )

    w.start()
    time.sleep(2.5)
    w.stop()

    assert len(batches) >= 1
    events = batches[0]
    assert len(events) == 2
    assert events[0]["role"] == "user"
    assert events[0]["tool_name"] == "cursor_db"
    assert events[1]["role"] == "assistant"

    # Cursor should be updated
    cursor = cm.get_cursor(str(user_dir))
    assert cursor.last_position > 0
