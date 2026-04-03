# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Commit tests"""

import asyncio
import json

import pytest

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session
from openviking_cli.exceptions import FailedPreconditionError


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    """Poll the task tracker until the task reaches a terminal state."""
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = tracker.get(task_id)
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

    async def test_commit_with_usage_records(self, client: AsyncOpenViking):
        """Test commit with usage records"""
        session = client.session(session_id="usage_commit_test")

        session.add_message("user", [TextPart("Test message")])
        session.used(contexts=["viking://user/test/resources/doc.md"])
        session.add_message("assistant", [TextPart("Response")])

        result = await session.commit_async()

        assert result.get("status") == "accepted"
        assert result.get("task_id") is not None

        # active_count_updated is now in the background task result
        task_result = await _wait_for_task(result["task_id"])
        assert task_result["status"] == "completed"

    async def test_active_count_incremented_after_commit(self, client_with_resource_sync: tuple):
        """Regression test: active_count must actually increment after commit.

        Previously _update_active_counts() had three bugs:
        1. Called storage.update() with MongoDB-style kwargs (filter=, update=)
           that don't match the actual signature update(collection, id, data),
           causing a silent TypeError on every commit.
        2. Used $inc syntax which storage.update() does not support (merge semantics
           require a plain value, not an increment operator).
        3. Used fetch_by_uri() to locate the record, but that method's path-field
           filter returns the entire subtree (hierarchical match), so any URI that
           has child records triggers a 'Duplicate records found' error and returns
           None — leaving active_count un-updated even after fixes 1 and 2.

        Fix: use storage.filter() to look up the record by URI and read
        its stored id, then call storage.update() with that id.
        """
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
