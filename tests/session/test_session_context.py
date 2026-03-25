# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Context retrieval tests"""

import asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker
from openviking.session import Session


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


class TestGetContextForSearch:
    """Test get_context_for_search"""

    async def test_get_context_basic(self, session_with_messages: Session):
        """Test basic context retrieval"""
        context = await session_with_messages.get_context_for_search(query="testing help")

        assert isinstance(context, dict)
        assert "latest_archive_overview" in context
        assert "current_messages" in context

    async def test_get_context_with_max_messages(self, session_with_messages: Session):
        """Test limiting max messages"""
        context = await session_with_messages.get_context_for_search(query="test", max_messages=2)

        assert isinstance(context, dict)
        assert len(context["current_messages"]) <= 2

    async def test_get_context_returns_latest_completed_archive_only(self, client: AsyncOpenViking):
        """Current context should expose only the latest completed archive overview."""
        session = client.session(session_id="archive_context_test")

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result1 = await session.commit_async()
        await _wait_for_task(result1["task_id"])

        session.add_message("user", [TextPart("Second message")])
        session.add_message("assistant", [TextPart("Second response")])
        session.add_message("user", [TextPart("Third message")])
        result2 = await session.commit_async()
        await _wait_for_task(result2["task_id"])
        latest_overview = await session._viking_fs.read_file(
            f"{result2['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )

        session.add_message("user", [TextPart("Current message")])
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == latest_overview
        assert len(context["current_messages"]) == 1

    async def test_get_context_skips_incomplete_latest_archive(self, client: AsyncOpenViking):
        """Incomplete archives without .done must not replace the latest completed overview."""
        session = client.session(session_id="archive_context_incomplete_test")

        session.add_message("user", [TextPart("First message")])
        session.add_message("assistant", [TextPart("First response")])
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        completed_overview = await session._viking_fs.read_file(
            f"{result['archive_uri']}/.overview.md",
            ctx=session.ctx,
        )
        await session._viking_fs.write_file(
            uri=f"{session.uri}/history/archive_999/.overview.md",
            content="INCOMPLETE OVERVIEW",
            ctx=session.ctx,
        )

        context = await session.get_context_for_search(query="test")

        assert context["latest_archive_overview"] == completed_overview

    async def test_get_context_empty_session(self, session: Session):
        """Test getting context from empty session"""
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"] == ""
        assert context["current_messages"] == []

    async def test_get_context_after_commit(self, client: AsyncOpenViking):
        """Test getting context after commit"""
        session = client.session(session_id="post_commit_context_test")

        # Add messages
        session.add_message("user", [TextPart("Test message before commit")])
        session.add_message("assistant", [TextPart("Response before commit")])

        # Commit
        result = await session.commit_async()
        await _wait_for_task(result["task_id"])

        # Add new messages
        session.add_message("user", [TextPart("New message after commit")])

        # Getting context should include archive summary
        context = await session.get_context_for_search(query="test")

        assert isinstance(context, dict)
        assert context["latest_archive_overview"]
        assert len(context["current_messages"]) == 1
