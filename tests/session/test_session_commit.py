# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Commit tests"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking import AsyncOpenViking
from openviking.client.session import Session as ClientSession
from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking.storage.transaction import get_lock_manager
from openviking_cli.exceptions import FailedPreconditionError


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    """Poll the task tracker until the task reaches a terminal state."""
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


class TestCommit:
    """Test commit"""

    async def test_commit_success(self, session_with_messages: Session):
        """Test successful commit returns accepted with task_id"""
        result = await session_with_messages.commit_async()

        assert isinstance(result, dict)
        assert result.get("status") == "accepted"
        assert "session_id" in result
        assert result.get("task_id") is not None
        assert "memories_extracted" not in result

    async def test_commit_extracts_memories(
        self, session_with_messages: Session, client: AsyncOpenViking
    ):
        """Test commit kicks off background memory extraction"""
        result = await session_with_messages.commit_async()
        task_id = result["task_id"]

        # Wait for background memory extraction to complete
        task_result = await _wait_for_task(task_id)
        assert task_result["status"] == "completed"
        assert "memories_extracted" in task_result["result"]
        memory_counts = task_result["result"]["memories_extracted"]
        assert isinstance(memory_counts, dict)

        # Wait for semantic/embedding queues
        await client.wait_processed(timeout=60.0)

    async def test_commit_reports_session_skills_separately(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = False
        config.memory.session_skill_extraction_enabled = True
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )
        if hasattr(session_with_messages._session_compressor, "extract_agent_memories"):
            session_with_messages._session_compressor.extract_agent_memories = AsyncMock(
                return_value={
                    "contexts": [],
                    "session_skills": [{"uri": "viking://account/test/agent/skills/code-review"}],
                }
            )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["memories_extracted"] == {}
        assert task_result["result"]["session_skills_extracted"] == 1
        assert task_result["result"]["session_skill_uris"] == [
            "viking://account/test/agent/skills/code-review"
        ]
        session_with_messages._session_compressor.extract_long_term_memories.assert_not_awaited()
        session_with_messages._session_compressor.extract_agent_memories.assert_awaited_once()

    async def test_commit_skips_session_skill_extraction_when_disabled(
        self, session_with_messages: Session, monkeypatch
    ):
        config = MagicMock()
        config.memory.extraction_enabled = True
        config.memory.session_skill_extraction_enabled = False
        monkeypatch.setattr("openviking.session.session.get_openviking_config", lambda: config)

        session_with_messages._session_compressor.extract_long_term_memories = AsyncMock(
            return_value=[]
        )
        if hasattr(session_with_messages._session_compressor, "extract_agent_memories"):
            session_with_messages._session_compressor.extract_agent_memories = AsyncMock(
                return_value={"contexts": [], "session_skills": []}
            )

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert task_result["result"]["session_skills_extracted"] == 0
        assert task_result["result"]["session_skill_uris"] == []
        session_with_messages._session_compressor.extract_long_term_memories.assert_awaited_once()
        session_with_messages._session_compressor.extract_agent_memories.assert_awaited_once()

    async def test_commit_archives_messages(self, session_with_messages: Session):
        """Test commit archives messages"""
        initial_message_count = len(session_with_messages.messages)
        assert initial_message_count > 0

        result = await session_with_messages.commit_async()

        assert result.get("archived") is True
        # Current message list should be cleared after commit
        assert len(session_with_messages.messages) == 0

    async def test_commit_empty_session(self, session: Session):
        """Test committing empty session"""
        # Empty session commit should not raise error
        result = await session.commit_async()

        assert isinstance(result, dict)
        assert result.get("archived") is False

    async def test_commit_multiple_times(self, client: AsyncOpenViking):
        """Test multiple commits"""
        session = client.session(session_id="multi_commit_test")

        # First round of conversation
        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async()
        assert result1.get("status") == "accepted"
        assert result1.get("task_id") is not None

        # Wait for first commit's background task to finish
        await _wait_for_task(result1["task_id"])

        # Second round of conversation
        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async()
        assert result2.get("status") == "accepted"
        assert result2.get("task_id") is not None

    async def test_commit_keep_recent_count_retains_live_tail_and_resets_pending_tokens(
        self, client: AsyncOpenViking
    ):
        session = client.session(session_id="commit_keep_recent_count_test")

        session.add_message("user", [TextPart("Round 1 user")])
        session.add_message("assistant", [TextPart("Round 1 assistant")])
        session.add_message("user", [TextPart("Round 2 user")])
        session.add_message("assistant", [TextPart("Round 2 assistant")])

        result = await session.commit_async(keep_recent_count=2)
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        assert len(session.messages) == 2
        assert [message.parts[0].text for message in session.messages] == [
            "Round 2 user",
            "Round 2 assistant",
        ]

        session_info = await client.get_session(session.session_id)
        assert session_info["pending_tokens"] == 0

        context = await session.get_session_context()
        assert context["latest_archive_overview"]
        assert [message["parts"][0]["text"] for message in context["messages"]] == [
            "Round 2 user",
            "Round 2 assistant",
        ]

    async def test_session_commit_keeps_telemetry_as_first_positional_argument(self):
        calls = []

        class _FakeClient:
            async def commit_session(self, session_id, telemetry=False, *, keep_recent_count=0):
                calls.append(
                    {
                        "session_id": session_id,
                        "telemetry": telemetry,
                        "keep_recent_count": keep_recent_count,
                    }
                )
                return {"task_id": "task-1"}

        session = ClientSession(_FakeClient(), "s1", "user-1")

        result = await session.commit(True)

        assert result == {"task_id": "task-1"}
        assert calls == [
            {
                "session_id": "s1",
                "telemetry": True,
                "keep_recent_count": 0,
            }
        ]

    async def test_session_commit_async_keeps_telemetry_as_first_positional_argument(self):
        calls = []

        class _FakeClient:
            async def commit_session(self, session_id, telemetry=False, *, keep_recent_count=0):
                calls.append(
                    {
                        "session_id": session_id,
                        "telemetry": telemetry,
                        "keep_recent_count": keep_recent_count,
                    }
                )
                return {"task_id": "task-1"}

        session = ClientSession(_FakeClient(), "s1", "user-1")

        result = await session.commit_async(True)

        assert result == {"task_id": "task-1"}
        assert calls == [
            {
                "session_id": "s1",
                "telemetry": True,
                "keep_recent_count": 0,
            }
        ]

    async def test_commit_uses_latest_archive_overview_for_summary_and_extraction(
        self, client: AsyncOpenViking
    ):
        """Second commit should pass the latest completed archive overview into Phase 2."""
        session = client.session(session_id="latest_overview_threading_test")

        session.add_message("user", [TextPart("First round message")])
        session.add_message("assistant", [TextPart("First round response")])
        result1 = await session.commit_async()
        await _wait_for_task(result1["task_id"])

        previous_overview = await session._viking_fs.read_file(
            f"{result1['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        seen: dict[str, str] = {}

        original_generate = session._generate_archive_summary_async

        async def capture_generate(messages, latest_archive_overview=""):
            seen["summary"] = latest_archive_overview
            return await original_generate(
                messages, latest_archive_overview=latest_archive_overview
            )

        async def capture_extract(*args, **kwargs):
            seen["extract"] = kwargs.get("latest_archive_overview", "")
            return []

        session._generate_archive_summary_async = capture_generate
        session._session_compressor.extract_long_term_memories = capture_extract

        session.add_message("user", [TextPart("Second round message")])
        session.add_message("assistant", [TextPart("Second round response")])
        result2 = await session.commit_async()
        task_result = await _wait_for_task(result2["task_id"])

        assert task_result["status"] == "completed"
        assert seen["summary"] == previous_overview
        assert seen["extract"] == previous_overview

    async def test_active_count_incremented_after_commit(self, client_with_resource_sync: tuple):
        client, uri = client_with_resource_sync
        vikingdb = client._client.service.vikingdb_manager
        # Use the client's own context to match the account_id used when adding the resource
        client_ctx = client._client._ctx

        # Look up the record by URI
        records_before = await vikingdb.get_context_by_uri(
            uri=uri,
            limit=1,
            ctx=client_ctx,
        )
        assert records_before, f"Resource not found for URI: {uri}"
        count_before = records_before[0].get("active_count") or 0

        # Mark as used and commit
        session = client.session(session_id="active_count_regression_test")
        session.add_message("user", [TextPart("Query")])
        session.used(contexts=[uri])
        session.add_message("assistant", [TextPart("Answer")])
        result = await session.commit_async()

        # Wait for background task to complete (active_count is updated there)
        task_result = await _wait_for_task(result["task_id"])
        assert task_result["status"] == "completed"
        assert task_result["result"]["active_count_updated"] == 1

        # Verify the count actually changed in storage
        records_after = await vikingdb.get_context_by_uri(
            uri=uri,
            limit=1,
            ctx=client_ctx,
        )
        assert records_after, f"Record disappeared after commit for URI: {uri}"
        count_after = records_after[0].get("active_count") or 0
        assert count_after == count_before + 1, (
            f"active_count not incremented: before={count_before}, after={count_after}"
        )

    async def test_commit_blocks_after_failed_archive(self, client: AsyncOpenViking):
        """A failed archive should block the next commit until it is resolved."""
        session = client.session(session_id="failed_archive_blocks_new_commit")

        async def failing_extract(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("synthetic extraction failure")

        session._session_compressor.extract_long_term_memories = failing_extract

        session.add_message("user", [TextPart("First round message")])
        result = await session.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "failed"

        failed_marker = await session._viking_fs.read_file(
            f"{result['archive_uri']}/.failed.json",
            ctx=session.ctx,
        )
        failed_payload = json.loads(failed_marker)
        assert failed_payload["stage"] == "memory_extraction"
        assert "synthetic extraction failure" in failed_payload["error"]

        session.add_message("user", [TextPart("Second round message")])
        with pytest.raises(FailedPreconditionError, match="unresolved failed archive"):
            await session.commit_async()

    async def test_commit_skips_redo_when_recovery_disabled(
        self, session_with_messages: Session, monkeypatch: pytest.MonkeyPatch
    ):
        """Phase 2 should not write or clear redo markers when redo recovery is disabled."""

        redo_log = MagicMock()
        lock_manager = get_lock_manager()
        monkeypatch.setattr(lock_manager, "_redo_recovery_enabled", False)
        monkeypatch.setattr(lock_manager, "_redo_log", redo_log)

        result = await session_with_messages.commit_async()
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        redo_log.write_pending.assert_not_called()
        redo_log.mark_done.assert_not_called()
