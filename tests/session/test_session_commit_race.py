# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for session commit race condition fix (#580)."""

import asyncio
import json

import pytest

from openviking import AsyncOpenViking
from openviking.message import TextPart
from openviking.server.identity import RequestContext, Role
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking_cli.session.user_id import UserIdentifier


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
            return_exceptions=True,
        )

        archived_count = sum(
            1 for r in results if isinstance(r, dict) and r.get("archived") is True
        )
        assert archived_count == 1, f"Expected exactly 1 archived commit, got {archived_count}"
        conflicts = [r for r in results if isinstance(r, LockAcquisitionError)]
        assert len(conflicts) == 1

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

    async def test_message_append_conflicts_with_auto_commit_live_rewrite_lock_without_data_loss(
        self, client: AsyncOpenViking
    ):
        """Auto-commit sessions serialize append with live rewrite to avoid data loss."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_auto_commit_append_lock"
        committing = await client._client._service.sessions.create(ctx, session_id=session_id)
        committing.meta.auto_commit_policy = {"enabled": True, "keep_recent_count": 0}
        await committing._save_meta()
        appending = client._client._service.sessions.session(ctx, session_id)

        committing.add_message("user", [TextPart("Original message")])
        await appending.load()

        rewrite_started = asyncio.Event()
        allow_rewrite = asyncio.Event()
        original_write = committing._write_to_agfs_async

        async def gated_write(messages):
            rewrite_started.set()
            await allow_rewrite.wait()
            await original_write(messages)

        committing._write_to_agfs_async = gated_write

        commit_task = asyncio.create_task(committing.commit_async())
        await rewrite_started.wait()

        try:
            await asyncio.to_thread(
                appending.add_message,
                "user",
                [TextPart("New message during commit rewrite")],
            )
        except LockAcquisitionError:
            pass
        else:
            raise AssertionError("add_message should fail while commit rewrite holds the lock")

        allow_rewrite.set()
        result = await commit_task
        appending.add_message("user", [TextPart("New message after commit rewrite")])

        assert result.get("archived") is True
        reloaded = client._client._service.sessions.session(ctx, session_id)
        await reloaded.load()
        assert [message.content for message in reloaded.messages] == [
            "New message after commit rewrite"
        ]

    async def test_message_append_does_not_use_commit_lock_without_auto_commit(
        self, client: AsyncOpenViking
    ):
        """Normal sessions keep the old append behavior and do not take the commit lock."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_plain_append_no_lock"
        committing = await client._client._service.sessions.create(ctx, session_id=session_id)
        appending = client._client._service.sessions.session(ctx, session_id)

        committing.add_message("user", [TextPart("Original message")])
        await appending.load()

        rewrite_started = asyncio.Event()
        allow_rewrite = asyncio.Event()
        original_write = committing._write_to_agfs_async

        async def gated_write(messages):
            rewrite_started.set()
            await allow_rewrite.wait()
            await original_write(messages)

        committing._write_to_agfs_async = gated_write

        commit_task = asyncio.create_task(committing.commit_async())
        await rewrite_started.wait()

        await asyncio.to_thread(
            appending.add_message,
            "user",
            [TextPart("New message during plain commit rewrite")],
        )

        allow_rewrite.set()
        result = await commit_task

        assert result.get("archived") is True

    async def test_commit_lock_conflict_preserves_pending_tokens(self, client: AsyncOpenViking):
        """A commit lock conflict is retryable and must not clear pending tokens."""
        session = client.session(session_id="race_test_commit_lock_conflict")
        session.add_message("user", [TextPart("Original message")])
        session.meta.pending_tokens = 123
        await session._save_meta()

        session_path = session._viking_fs._uri_to_path(session._session_uri, ctx=session.ctx)
        async with LockContext(get_lock_manager(), [session_path], lock_mode="exact"):
            with pytest.raises(LockAcquisitionError):
                await session.commit_async()

        assert session.meta.pending_tokens == 123

        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        assert json.loads(raw_meta)["pending_tokens"] == 123
