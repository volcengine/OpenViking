# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for session commit race condition fix (#580)."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from openviking.message import TextPart
from openviking.service.task_tracker import get_task_tracker


class TestCommitRace:
    """Test concurrent commit safety."""

    async def test_message_added_during_commit_not_lost(
        self,
        client,
        monkeypatch,
    ):
        """Messages added while commit is running should not be lost."""
        session = client(session_id="race_test_msg_safety")
        await session.ensure_exists()
        original = await session.add_message_async("user", [TextPart("Original message")])
        adding = client(session_id=session.session_id)
        await adding.load()
        summary_started, release_summary = asyncio.Event(), asyncio.Event()

        async def complete_summary(**_kwargs):
            summary_started.set()
            await release_summary.wait()
            return "# Working Memory\n\nOriginal message."

        vlm = SimpleNamespace(
            is_available=lambda: True, max_tokens=4096, get_completion_async=complete_summary
        )
        monkeypatch.setattr(type(session), "_get_vlm_config", AsyncMock(return_value=vlm))

        commit = await session.commit_async(
            enable_working_memory=True,
            memory_policy={"self": {"enabled": False}, "peer": {"enabled": False}},
        )
        try:
            await asyncio.wait_for(summary_started.wait(), timeout=5)
            added = await asyncio.wait_for(
                adding.add_message_async("user", [TextPart("New message during commit")]),
                timeout=5,
            )
        finally:
            release_summary.set()

        task = await get_task_tracker().wait(commit["task_id"], timeout=5)
        assert task.status.value == "completed", task.error
        fresh = client(session_id=session.session_id)
        await fresh.load()
        archive = await fresh.get_session_archive(commit["archive_uri"].rsplit("/", 1)[-1])
        assert [message["id"] for message in archive["messages"]] == [original.id]
        assert [message.id for message in fresh.messages] == [added.id]
