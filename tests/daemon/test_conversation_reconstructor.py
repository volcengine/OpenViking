"""Tests for ConversationReconstructor."""
from openviking.daemon.conversation_reconstructor import ConversationReconstructor


def test_reconstruct_simple_conversation():
    r = ConversationReconstructor()
    events = [
        {"role": "user", "content": "How to configure PostgreSQL?", "timestamp": "2026-06-15T10:00:00Z"},
        {"role": "assistant", "content": "Edit postgresql.conf", "timestamp": "2026-06-15T10:00:01Z"},
    ]
    turns = r.reconstruct(events)
    assert len(turns) == 1
    assert turns[0].user_prompt == "How to configure PostgreSQL?"
    assert turns[0].assistant_response == "Edit postgresql.conf"


def test_skip_orphaned_assistant():
    r = ConversationReconstructor()
    events = [
        {"role": "assistant", "content": "Orphan answer", "timestamp": "2026-06-15T10:00:00Z"},
    ]
    turns = r.reconstruct(events)
    assert len(turns) == 0


def test_multiple_turns():
    r = ConversationReconstructor()
    events = [
        {"role": "user", "content": "Q1", "timestamp": "2026-06-15T10:00:00Z"},
        {"role": "assistant", "content": "A1", "timestamp": "2026-06-15T10:00:01Z"},
        {"role": "user", "content": "Q2", "timestamp": "2026-06-15T10:00:02Z"},
        {"role": "assistant", "content": "A2", "timestamp": "2026-06-15T10:00:03Z"},
    ]
    turns = r.reconstruct(events)
    assert len(turns) == 2
    assert turns[0].user_prompt == "Q1"
    assert turns[1].user_prompt == "Q2"


def test_unpaired_user_prompt():
    r = ConversationReconstructor()
    events = [
        {"role": "user", "content": "No answer", "timestamp": "2026-06-15T10:00:00Z"},
    ]
    turns = r.reconstruct(events)
    assert len(turns) == 0


def test_preserves_metadata():
    r = ConversationReconstructor()
    events = [
        {"role": "user", "content": "Q", "timestamp": "2026-06-15T10:00:00Z", "session_id": "s1", "project_name": "proj"},
        {"role": "assistant", "content": "A", "timestamp": "2026-06-15T10:00:01Z"},
    ]
    turns = r.reconstruct(events)
    assert turns[0].session_id == "s1"
    assert turns[0].project_name == "proj"
    assert turns[0].timestamp == "2026-06-15T10:00:00Z"
