# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Tests for session commit race condition fix (#580)."""

import asyncio

from openviking import AsyncOpenViking
from openviking.message import TextPart


class TestCommitRace:
    """Test concurrent commit safety."""

    async def test_concurrent_commit_no_duplicate(self, client: AsyncOpenViking):
        """Two concurrent commits on the same session: only one should archive."""
        session = client.session(session_id="race_test_dedup")
        session.add_message("user", [TextPart("Hello")])
        session.add_message("assistant", [TextPart("Hi there")])

        results = await asyncio.gather(
            session.commit_async(),
            session.commit_async(),
        )

        archived_count = sum(1 for r in results if r.get("archived") is True)
        assert archived_count == 1, f"Expected exactly 1 archived commit, got {archived_count}"

        # Messages should be cleared after commit
        assert len(session.messages) == 0

        # Compression index should have incremented exactly once
        assert session._compression.compression_index == 1

    async def test_message_added_during_commit_not_lost(self, client: AsyncOpenViking):
        """Messages added while commit is running should not be lost."""
        session = client.session(session_id="race_test_msg_safety")
        session.add_message("user", [TextPart("Original message")])

        # Use an Event for deterministic synchronization instead of sleeps
        phase1_done = asyncio.Event()
        original_generate = session._generate_archive_summary_async

        async def slow_generate(messages, latest_archive_overview=""):
            # Signal that Phase 1 is complete (lock released, messages cleared)
            phase1_done.set()
            # Yield control so add_message can run before archive completes
            await asyncio.sleep(0)
            return await original_generate(
                messages,
                latest_archive_overview=latest_archive_overview,
            )

        session._generate_archive_summary_async = slow_generate

        async def commit_and_add():
            """Start commit, then add a message after Phase 1 completes."""
            commit_task = asyncio.create_task(session.commit_async())
            # Wait until Phase 1 is done (lock released, messages cleared)
            await phase1_done.wait()
            # Add message while commit is in Phase 2 (after lock released)
            session.add_message("user", [TextPart("New message during commit")])
            return await commit_task

        result = await commit_and_add()

        assert result.get("archived") is True
        # The new message should still be in the session
        assert len(session.messages) == 1
        assert session.messages[0].content == "New message during commit"
