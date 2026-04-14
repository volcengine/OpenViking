# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from pathlib import Path

import pytest

from openviking.migration.openclaw import (
    discover_openclaw_memory_artifacts,
    discover_openclaw_transcript_sessions,
    migrate_openclaw,
    parse_openclaw_transcript,
)


def test_discover_openclaw_memory_artifacts(temp_dir: Path):
    workspace = temp_dir / "workspace"
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# Durable\n")
    (memory_dir / "2026-04-01.md").write_text("daily log\n")
    (memory_dir / "2026-04-01-project-alpha.md").write_text("case summary\n")

    artifacts = discover_openclaw_memory_artifacts(temp_dir)

    assert [artifact.category for artifact in artifacts] == ["entities", "events", "cases"]
    assert artifacts[0].uri == "viking://user/memories/entities/openclaw-memory.md"
    assert artifacts[1].uri == "viking://user/memories/events/openclaw-2026-04-01.md"
    assert artifacts[2].uri == "viking://agent/memories/cases/openclaw-2026-04-01-project-alpha.md"


def test_discover_openclaw_transcript_sessions_includes_index_and_orphans(temp_dir: Path):
    sessions_dir = temp_dir / "agents" / "main" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "from-index.jsonl").write_text("")
    (sessions_dir / "orphan.jsonl").write_text("")
    (sessions_dir / "sessions.json").write_text(
        '{"agent:main:test": {"sessionId": "from-index", "sessionFile": "from-index.jsonl", "label": "Indexed"}}'
    )

    sessions = discover_openclaw_transcript_sessions(temp_dir)

    assert [(session.session_id, session.label) for session in sessions] == [
        ("from-index", "Indexed"),
        ("orphan", ""),
    ]


def test_parse_openclaw_transcript_extracts_user_and_assistant_text(temp_dir: Path):
    transcript = temp_dir / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                '{"type":"header","version":1}',
                '{"type":"message","timestamp":1713072000000,"message":{"role":"user","content":[{"type":"text","text":"hello"}]}}',
                '{"type":"message","message":{"role":"assistant","content":[{"type":"output_text","text":"hi there"}]}}',
                '{"type":"message","message":{"role":"toolResult","content":"ignored"}}',
            ]
        )
    )

    messages = parse_openclaw_transcript(transcript)

    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "hello"
    assert messages[0].created_at is not None
    assert messages[1].content == "hi there"


def test_migrate_openclaw_dry_run_reports_memory_and_transcript(temp_dir: Path):
    workspace = temp_dir / "workspace"
    memory_dir = workspace / "memory"
    sessions_dir = temp_dir / "agents" / "main" / "sessions"
    memory_dir.mkdir(parents=True)
    sessions_dir.mkdir(parents=True)

    (workspace / "MEMORY.md").write_text("# Durable\n")
    (sessions_dir / "s1.jsonl").write_text(
        '{"type":"message","message":{"role":"user","content":[{"type":"text","text":"hello"}]}}'
    )
    (sessions_dir / "sessions.json").write_text(
        '{"agent:main:s1": {"sessionId": "s1", "sessionFile": "s1.jsonl"}}'
    )

    result = migrate_openclaw(object(), temp_dir, mode="all", dry_run=True)

    assert result["memory"]["summary"] == {
        "planned": 1,
        "imported": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert result["transcript"]["summary"] == {
        "planned": 1,
        "imported": 0,
        "skipped": 0,
        "failed": 0,
    }


def test_migrate_openclaw_rejects_async_clients(temp_dir: Path):
    workspace = temp_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "MEMORY.md").write_text("# Durable\n")

    class AsyncOnlyClient:
        async def stat(self, uri: str):
            del uri
            return {}

        async def import_memory(self, uri: str, content: str, **kwargs):
            del uri, content, kwargs
            return {"ok": True}

    with pytest.raises(TypeError, match="synchronous client"):
        migrate_openclaw(AsyncOnlyClient(), temp_dir, mode="memory", dry_run=False)
