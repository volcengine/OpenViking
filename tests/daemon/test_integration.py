"""Integration tests for OpenViking Active Daemon."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.daemon.cursor_manager import CursorManager
from openviking.daemon.deduplicator import KnowledgeDeduplicator
from openviking.daemon.etl_pipeline import BatchETLPipeline
from openviking.daemon.filters import LowValueFilter
from openviking.daemon.conversation_reconstructor import ConversationReconstructor
from openviking.daemon.knowledge_router import KnowledgeRouter
from openviking.daemon.storage_adapter import VikingStorageAdapter
from openviking.daemon.models import ExtractedKnowledge


@pytest.fixture
def temp_watch_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_jsonl_file_created_and_readable(temp_watch_dir):
    """Verify we can create and read JSONL files in the watch directory."""
    jsonl_file = temp_watch_dir / "test_session.jsonl"

    events = [
        {
            "timestamp": "2026-06-15T10:00:00Z",
            "role": "user",
            "content": "How to configure PostgreSQL for high availability?",
            "type": "message",
        },
        {
            "timestamp": "2026-06-15T10:00:01Z",
            "role": "assistant",
            "content": "Edit postgresql.conf, set max_connections=100 and enable WAL archiving",
            "type": "message",
        },
    ]

    with open(jsonl_file, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    assert jsonl_file.exists()
    lines = jsonl_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2

    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["role"] == "user"
    assert parsed[1]["role"] == "assistant"


def test_cursor_tracks_jsonl_progress(temp_watch_dir, temp_db):
    """Verify CursorManager correctly tracks incremental reads."""
    jsonl_file = temp_watch_dir / "session.jsonl"

    # Write first batch
    with open(jsonl_file, "w", encoding="utf-8") as f:
        f.write(json.dumps({"role": "user", "content": "First question", "type": "message"}) + "\n")

    cursor_mgr = CursorManager(temp_db)
    cursor = cursor_mgr.get_cursor(str(jsonl_file))
    assert cursor.last_position == 0

    # Read the file
    with open(jsonl_file, "r", encoding="utf-8") as f:
        f.seek(cursor.last_position)
        lines = f.readlines()
        new_position = f.tell()

    assert len(lines) == 1
    assert new_position > 0

    cursor_mgr.update_cursor(str(jsonl_file), new_position)

    # Verify cursor persisted
    cursor2 = cursor_mgr.get_cursor(str(jsonl_file))
    assert cursor2.last_position == new_position

    # Append more data
    with open(jsonl_file, "a", encoding="utf-8") as f:
        f.write(json.dumps({"role": "assistant", "content": "Answer", "type": "message"}) + "\n")

    # Read incrementally
    with open(jsonl_file, "r", encoding="utf-8") as f:
        f.seek(cursor2.last_position)
        new_lines = f.readlines()

    assert len(new_lines) == 1
    assert json.loads(new_lines[0])["role"] == "assistant"


def test_filter_and_reconstruct_pipeline():
    """Verify the filter -> reconstruct pipeline works end-to-end."""
    events = [
        {"role": "user", "content": "Hi", "type": "message", "timestamp": "2026-06-15T10:00:00Z"},  # Too short
        {"role": "user", "content": "npm install lodash --save-dev", "type": "message", "timestamp": "2026-06-15T10:00:01Z"},  # Noise
        {"role": "user", "content": "How should we handle database migrations in production?", "type": "message", "timestamp": "2026-06-15T10:00:02Z"},
        {"role": "assistant", "content": "Use Alembic with versioned migration scripts and run them in a CI pipeline before deployment", "type": "message", "timestamp": "2026-06-15T10:00:03Z"},
    ]

    # Step 1: Filter
    filt = LowValueFilter()
    filtered = filt.apply(events)
    assert len(filtered) == 2  # "Hi" and "npm install" removed

    # Step 2: Reconstruct
    reconstructor = ConversationReconstructor()
    turns = reconstructor.reconstruct(filtered)
    assert len(turns) == 1
    assert "database migrations" in turns[0].user_prompt
    assert "Alembic" in turns[0].assistant_response


def test_knowledge_router_all_categories():
    """Verify router handles all categories correctly."""
    router = KnowledgeRouter()

    skill = ExtractedKnowledge(status="EXTRACTED", category="skills", title="PG Config", content="...", source_tool="claude_code")
    assert "skills/claude_code" in router.route(skill)

    mem_proj = ExtractedKnowledge(status="EXTRACTED", category="memories", title="Arch", content="...", project_name="myapp")
    assert "memories/myapp" in router.route(mem_proj)

    mem_global = ExtractedKnowledge(status="EXTRACTED", category="memories", title="General", content="...")
    assert "memories/global" in router.route(mem_global)

    resource = ExtractedKnowledge(status="EXTRACTED", category="resources", title="Redis", content="...", entity_links=["Redis"])
    assert "resources/Redis" in router.route(resource)


def test_deduplicator_prevents_duplicates():
    """Verify deduplicator blocks duplicate knowledge."""
    dedup = KnowledgeDeduplicator()

    k1 = ExtractedKnowledge(status="EXTRACTED", category="memories", title="A", content="Same content here")
    k2 = ExtractedKnowledge(status="EXTRACTED", category="memories", title="B", content="Same content here")
    k3 = ExtractedKnowledge(status="EXTRACTED", category="memories", title="C", content="Different content")

    assert not dedup.is_duplicate(k1)
    assert dedup.is_duplicate(k2)
    assert not dedup.is_duplicate(k3)


def test_storage_adapter_formats_content():
    """Verify storage adapter generates correct Markdown for each category."""
    mock_service = MagicMock()
    adapter = VikingStorageAdapter(mock_service)

    skill = ExtractedKnowledge(
        status="EXTRACTED", category="skills", title="Test Skill",
        content="Do this thing", confidence=0.9,
        actionable_steps=["Step 1", "Step 2"],
    )
    content = adapter._format_content(skill)
    assert "Test Skill" in content
    assert "Step 1" in content
    assert "Step 2" in content

    memory = ExtractedKnowledge(
        status="EXTRACTED", category="memories", title="Decision",
        content="We chose X", entity_links=["tag1"],
        timestamp="2026-06-15T10:00:00Z",
    )
    content = adapter._format_content(memory)
    assert "Decision" in content
    assert "tag1" in content

    resource = ExtractedKnowledge(
        status="EXTRACTED", category="resources", title="Guide",
        content="Reference material", entity_links=["Docker"],
    )
    content = adapter._format_content(resource)
    assert "Guide" in content
    assert "Docker" in content
