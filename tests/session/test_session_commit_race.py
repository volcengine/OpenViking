# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for session commit race condition fix (#580)."""

import asyncio
import json
import threading

import pytest

from openviking import AsyncOpenViking
from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.service.task_tracker import get_task_tracker
from openviking.storage.errors import LockAcquisitionError
from openviking.storage.transaction import LockContext, get_lock_manager
from openviking_cli.session.user_id import UserIdentifier


async def _wait_for_task(task_id: str, timeout: float = 30.0) -> dict:
    tracker = get_task_tracker()
    for _ in range(int(timeout / 0.1)):
        task = await tracker.get(task_id)
        if task and task.status.value in ("completed", "failed"):
            return task.to_dict()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"Task {task_id} did not complete within {timeout}s")


async def _marker_exists(session, archive_uri: str, name: str) -> bool:
    try:
        await session._viking_fs.read_file(f"{archive_uri}/{name}", ctx=session.ctx)
        return True
    except Exception:
        return False


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

        phase1_done = asyncio.Event()
        allow_meta_save = asyncio.Event()
        original_merge_meta = session._merge_and_save_commit_boundary_meta

        async def gated_merge_meta(**kwargs):
            phase1_done.set()
            await allow_meta_save.wait()
            return await original_merge_meta(**kwargs)

        session._merge_and_save_commit_boundary_meta = gated_merge_meta

        async def commit_and_add():
            """Start commit, then add a message after Phase 1 completes."""
            commit_task = asyncio.create_task(session.commit_async())
            # Wait until Phase 1 is done (lock released, messages cleared)
            await phase1_done.wait()
            # Add message while commit is in Phase 2 (after lock released)
            session.add_message("user", [TextPart("New message during commit")])
            allow_meta_save.set()
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
        committing.meta.auto_commit_policy = {"keep_recent_count": 0}
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

    async def test_commit_reloads_authoritative_messages_under_lock(
        self, client: AsyncOpenViking
    ):
        """A stale Session instance must not trim live messages added by another instance."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_commit_reloads_under_lock"
        stale = await client._client._service.sessions.create(ctx, session_id=session_id)
        stale.add_message("user", [TextPart("Original message")])

        current = client._client._service.sessions.session(ctx, session_id)
        await current.load()
        current.add_message("user", [TextPart("New message from another instance")])

        result = await stale.commit_async()

        assert result.get("archived") is True
        reloaded = client._client._service.sessions.session(ctx, session_id)
        await reloaded.load()
        raw_archive = await stale._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl",
            ctx=stale.ctx,
        )
        archived_messages = [
            Message.from_dict(json.loads(line))
            for line in raw_archive.strip().split("\n")
            if line.strip()
        ]
        persisted_contents = [message.content for message in archived_messages + reloaded.messages]
        assert persisted_contents == [
            "Original message",
            "New message from another instance",
        ]

    async def test_empty_stale_commit_reloads_authoritative_messages(
        self, client: AsyncOpenViking
    ):
        """Even an empty in-memory Session must check persisted messages under the lock."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_empty_stale_commit_reloads"
        stale = client._client._service.sessions.session(ctx, session_id)

        current = await client._client._service.sessions.create(ctx, session_id=session_id)
        current.add_message("user", [TextPart("Message only on disk")])

        result = await stale.commit_async()

        assert result.get("archived") is True
        raw_archive = await stale._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl",
            ctx=stale.ctx,
        )
        assert "Message only on disk" in raw_archive

    async def test_commit_treats_missing_live_messages_file_as_empty(
        self, client: AsyncOpenViking
    ):
        """Existing session roots without messages.jsonl should still commit as empty."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_missing_live_messages_file"
        session = await client._client._service.sessions.create(ctx, session_id=session_id)
        await session._viking_fs.rm(
            f"{session._session_uri}/messages.jsonl",
            ctx=session.ctx,
        )

        result = await session.commit_async()

        assert result.get("archived") is False
        assert result.get("reason") == "no_messages"

    async def test_stale_commit_uses_next_archive_index_when_meta_lags(
        self, client: AsyncOpenViking
    ):
        """A stale Session must not overwrite an archive created before meta is updated."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_stale_commit_archive_index"
        committing = await client._client._service.sessions.create(ctx, session_id=session_id)
        committing.add_message("user", [TextPart("First archive message")])

        stale = client._client._service.sessions.session(ctx, session_id)
        await stale.load()

        phase1_done = asyncio.Event()
        allow_meta_save = asyncio.Event()
        original_merge_meta = committing._merge_and_save_commit_boundary_meta

        async def gated_merge_meta(**kwargs):
            phase1_done.set()
            await allow_meta_save.wait()
            return await original_merge_meta(**kwargs)

        committing._merge_and_save_commit_boundary_meta = gated_merge_meta

        first_commit = asyncio.create_task(committing.commit_async())
        await phase1_done.wait()

        appending = client._client._service.sessions.session(ctx, session_id)
        await appending.load()
        appending.add_message("user", [TextPart("Second archive message")])

        second_result = await stale.commit_async()
        allow_meta_save.set()
        first_result = await first_commit

        assert first_result.get("archive_uri", "").endswith("archive_001")
        assert second_result.get("archive_uri", "").endswith("archive_002")

        first_archive = await stale._viking_fs.read_file(
            f"{first_result['archive_uri']}/messages.jsonl",
            ctx=stale.ctx,
        )
        second_archive = await stale._viking_fs.read_file(
            f"{second_result['archive_uri']}/messages.jsonl",
            ctx=stale.ctx,
        )
        assert "First archive message" in first_archive
        assert "Second archive message" in second_archive

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

    async def test_auto_commit_success_meta_merge_preserves_concurrent_message_meta(
        self, client: AsyncOpenViking
    ):
        """Auto-commit success fields must not overwrite newer add_message meta."""
        session = client.session(session_id="race_test_auto_commit_success_meta_merge")
        session.add_message("user", [TextPart("Original message")])

        original_write_live = session._write_to_agfs_async
        concurrent_last_message_at = "2099-01-01T00:00:00.000Z"
        concurrent_msg = Message(
            id="msg_concurrent_meta_merge",
            role="user",
            parts=[TextPart("Concurrent message during commit meta save")],
            created_at=concurrent_last_message_at,
        )

        async def write_live_then_concurrent_meta(messages):
            await original_write_live(messages)
            await session._viking_fs.write_file(
                f"{session._session_uri}/messages.jsonl",
                concurrent_msg.to_jsonl() + "\n",
                ctx=session.ctx,
            )
            latest = session.meta.to_dict()
            latest.update(
                {
                    "message_count": 1,
                    "pending_tokens": 77,
                    "last_message_at": concurrent_last_message_at,
                }
            )
            await session._viking_fs.write_file(
                f"{session._session_uri}/.meta.json",
                json.dumps(latest),
                ctx=session.ctx,
            )

        session._write_to_agfs_async = write_live_then_concurrent_meta

        result = await session.commit_async(record_auto_commit_success=True)

        assert result.get("archived") is True
        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        meta = json.loads(raw_meta)
        assert meta["message_count"] == 1
        assert meta["pending_tokens"] == concurrent_msg.estimated_tokens
        assert meta["last_message_at"] == concurrent_last_message_at
        assert meta["last_auto_commit_at"] != ""
        assert meta["auto_commit_last_error"] == ""

    async def test_commit_boundary_meta_save_preserves_message_meta_written_after_count_read(
        self, client: AsyncOpenViking
    ):
        """Meta merge must not overwrite add_message meta written after live count is read."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session = await client._client._service.sessions.create(
            ctx,
            session_id="race_test_commit_meta_save_window",
        )
        session.add_message("user", [TextPart("Original message")])
        await session._viking_fs.write_file(
            f"{session._session_uri}/messages.jsonl",
            "",
            ctx=session.ctx,
        )

        original_read_live_messages = session._read_live_messages
        concurrent_last_message_at = "2099-01-01T00:00:00.000Z"
        concurrent_msg = Message(
            id="msg_concurrent_meta_after_count",
            role="user",
            parts=[TextPart("Concurrent message after live read starts")],
            created_at=concurrent_last_message_at,
        )

        async def read_messages_then_concurrent_meta():
            await original_read_live_messages()
            await session._viking_fs.write_file(
                f"{session._session_uri}/messages.jsonl",
                concurrent_msg.to_jsonl() + "\n",
                ctx=session.ctx,
            )
            latest = session.meta.to_dict()
            latest.update(
                {
                    "message_count": 1,
                    "pending_tokens": 88,
                    "last_message_at": concurrent_last_message_at,
                }
            )
            await session._viking_fs.write_file(
                f"{session._session_uri}/.meta.json",
                json.dumps(latest),
                ctx=session.ctx,
            )
            return await original_read_live_messages()

        session._read_live_messages = read_messages_then_concurrent_meta

        await session._merge_and_save_commit_boundary_meta(
            archive_index=1,
            retained_message_count=0,
            stored_keep_recent_count=0,
            record_auto_commit_success=True,
        )
        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        meta = json.loads(raw_meta)
        assert meta["message_count"] == 1
        assert meta["pending_tokens"] == concurrent_msg.estimated_tokens
        assert meta["last_message_at"] == concurrent_last_message_at
        assert meta["last_auto_commit_at"] != ""

    async def test_phase2_commit_meta_merge_preserves_message_meta_written_after_meta_read(
        self, client: AsyncOpenViking
    ):
        """Phase 2 meta merge must not clobber add_message meta after its meta read."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session = await client._client._service.sessions.create(
            ctx,
            session_id="race_test_phase2_meta_save_window",
        )

        original_read_live_message_count = session._read_live_message_count
        concurrent_last_message_at = "2099-01-02T00:00:00.000Z"

        concurrent_msg = Message(
            id="msg_phase2_concurrent_meta",
            role="user",
            parts=[TextPart("Concurrent message during phase2 meta merge")],
            created_at=concurrent_last_message_at,
        )

        async def read_count_after_concurrent_meta():
            await session._viking_fs.write_file(
                f"{session._session_uri}/messages.jsonl",
                concurrent_msg.to_jsonl() + "\n",
                ctx=session.ctx,
            )
            latest = session.meta.to_dict()
            latest.update(
                {
                    "message_count": 1,
                    "pending_tokens": 99,
                    "last_message_at": concurrent_last_message_at,
                }
            )
            await session._viking_fs.write_file(
                f"{session._session_uri}/.meta.json",
                json.dumps(latest),
                ctx=session.ctx,
            )
            return await original_read_live_message_count()

        session._read_live_message_count = read_count_after_concurrent_meta

        await session._merge_and_save_commit_meta(
            archive_index=1,
            memories_extracted={},
            telemetry_snapshot=None,
        )

        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        meta = json.loads(raw_meta)
        assert meta["message_count"] == 1
        assert meta["pending_tokens"] == concurrent_msg.estimated_tokens
        assert meta["last_message_at"] == concurrent_last_message_at
        assert meta["commit_count"] == 1

    async def test_commit_boundary_meta_recomputes_pending_tokens_for_final_keep_window(
        self, client: AsyncOpenViking
    ):
        """Boundary meta must rebuild pending_tokens after changing keep_recent_count."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session = await client._client._service.sessions.create(
            ctx,
            session_id="race_test_boundary_pending_final_keep",
        )
        messages = [
            Message(id="msg_retained_1", role="user", parts=[TextPart("retained one")]),
            Message(id="msg_retained_2", role="assistant", parts=[TextPart("retained two")]),
            Message(id="msg_appended_3", role="user", parts=[TextPart("appended three")]),
        ]
        await session._viking_fs.write_file(
            f"{session._session_uri}/messages.jsonl",
            "".join(message.to_jsonl() + "\n" for message in messages),
            ctx=session.ctx,
        )
        meta = session.meta.to_dict()
        meta.update(
            {
                "message_count": 3,
                "keep_recent_count": 10,
                "pending_tokens": 0,
                "last_message_at": "2099-01-03T00:00:00.000Z",
            }
        )
        await session._viking_fs.write_file(
            f"{session._session_uri}/.meta.json",
            json.dumps(meta),
            ctx=session.ctx,
        )

        await session._merge_and_save_commit_boundary_meta(
            archive_index=1,
            retained_message_count=2,
            stored_keep_recent_count=2,
            record_auto_commit_success=False,
        )

        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        saved = json.loads(raw_meta)
        assert saved["message_count"] == 3
        assert saved["keep_recent_count"] == 2
        assert saved["pending_tokens"] == messages[0].estimated_tokens

    async def test_auto_append_holds_commit_lock_until_meta_saved(
        self, client: AsyncOpenViking
    ):
        """Auto append JSONL and meta save must be atomic against commit rewrites."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_auto_append_meta_under_lock"
        appending = await client._client._service.sessions.create(ctx, session_id=session_id)
        appending.meta.auto_commit_policy = {"keep_recent_count": 0}
        await appending._save_meta()

        save_entered = threading.Event()
        allow_save = threading.Event()
        original_append_save_meta = appending._save_meta

        async def gated_append_save_meta():
            save_entered.set()
            allow_save.wait(timeout=5)
            await original_append_save_meta()

        appending._save_meta = gated_append_save_meta

        append_task = asyncio.create_task(asyncio.to_thread(
            appending.add_message,
            "user",
            [TextPart("Append whose meta save is still pending")],
        ))
        await asyncio.to_thread(save_entered.wait, 5)

        committing = client._client._service.sessions.session(ctx, session_id)
        await committing.load()
        with pytest.raises(LockAcquisitionError):
            await committing.commit_async()

        allow_save.set()
        await append_task

    async def test_commit_boundary_meta_waits_for_auto_append_lock_after_phase1(
        self, client: AsyncOpenViking
    ):
        """Commit boundary meta save should wait for a short auto-append lock."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_commit_meta_same_lock"
        committing = await client._client._service.sessions.create(ctx, session_id=session_id)
        committing.meta.auto_commit_policy = {"keep_recent_count": 0}
        await committing._save_meta()
        committing.add_message("user", [TextPart("Message to archive")])

        appending = client._client._service.sessions.session(ctx, session_id)
        await appending.load()
        appending.meta.auto_commit_policy = {"keep_recent_count": 0}

        save_entered = threading.Event()
        allow_save = threading.Event()
        original_append_save_meta = appending._save_meta

        async def gated_append_save_meta():
            save_entered.set()
            allow_save.wait(timeout=5)
            await original_append_save_meta()

        appending._save_meta = gated_append_save_meta

        original_merge_meta = committing._merge_and_save_commit_boundary_meta

        async def append_then_merge(**kwargs):
            append_task = asyncio.create_task(asyncio.to_thread(
                appending.add_message,
                "user",
                [TextPart("Message appended before boundary meta save")],
            ))
            await asyncio.to_thread(save_entered.wait, 5)
            merge_task = asyncio.create_task(original_merge_meta(**kwargs))
            await asyncio.sleep(0)
            assert not merge_task.done()
            allow_save.set()
            result = await merge_task
            await append_task
            return result

        committing._merge_and_save_commit_boundary_meta = append_then_merge

        result = await committing.commit_async()

        assert result.get("archived") is True
        raw_meta = await committing._viking_fs.read_file(
            f"{committing._session_uri}/.meta.json",
            ctx=committing.ctx,
        )
        meta = json.loads(raw_meta)
        assert meta["message_count"] == 1
        assert meta["last_message_at"] != ""

    async def test_auto_append_merges_meta_from_authoritative_state_under_lock(
        self, client: AsyncOpenViking
    ):
        """Concurrent auto appends from stale Session instances must not regress meta."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_auto_append_authoritative_meta"
        created = await client._client._service.sessions.create(ctx, session_id=session_id)
        created.meta.auto_commit_policy = {"message_count_threshold": 10, "keep_recent_count": 0}
        await created._save_meta()

        first = client._client._service.sessions.session(ctx, session_id)
        second = client._client._service.sessions.session(ctx, session_id)
        await first.load()
        await second.load()

        first.add_message("user", [TextPart("first concurrent append")])
        second.add_message("user", [TextPart("second concurrent append")])

        raw_messages = await created._viking_fs.read_file(
            f"{created._session_uri}/messages.jsonl",
            ctx=created.ctx,
        )
        raw_meta = await created._viking_fs.read_file(
            f"{created._session_uri}/.meta.json",
            ctx=created.ctx,
        )
        persisted_count = len([line for line in raw_messages.splitlines() if line.strip()])
        meta = json.loads(raw_meta)

        assert persisted_count == 2
        assert meta["message_count"] == 2
        assert meta["pending_tokens"] > 0

    async def test_commit_enqueues_phase2_when_boundary_meta_lock_fails(
        self, client: AsyncOpenViking
    ):
        """Phase 1 archive success must still create a durable Phase 2 task."""
        session = client.session(session_id="race_test_boundary_meta_lock_failure")
        session.add_message("user", [TextPart("message that must be extracted")])

        async def fail_boundary_meta(**_kwargs):
            raise LockAcquisitionError("boundary meta lock busy")

        session._merge_and_save_commit_boundary_meta = fail_boundary_meta

        result = await session.commit_async()

        assert result.get("archived") is True
        assert result.get("status") == "accepted"
        assert result.get("task_id")
        raw_archive = await session._viking_fs.read_file(
            f"{result['archive_uri']}/messages.jsonl",
            ctx=session.ctx,
        )
        assert "message that must be extracted" in raw_archive

    async def test_phase2_recovers_boundary_meta_when_phase1_boundary_meta_save_fails(
        self, client: AsyncOpenViking
    ):
        """Durable Phase 2 payload must restore commit boundary meta if Phase 1 meta fails."""
        session = client.session(session_id="race_test_boundary_meta_recovered_by_phase2")
        messages = [
            Message(id="msg_archive", role="user", parts=[TextPart("archive me")]),
            Message(id="msg_retained_1", role="assistant", parts=[TextPart("retain one")]),
            Message(id="msg_retained_2", role="user", parts=[TextPart("retain two")]),
        ]
        for message in messages:
            session.add_message(message.role, message.parts)
        session.meta.keep_recent_count = 10
        session.meta.pending_tokens = 0
        await session._save_meta()

        async def fail_boundary_meta(**_kwargs):
            raise LockAcquisitionError("boundary meta lock busy")

        session._merge_and_save_commit_boundary_meta = fail_boundary_meta

        result = await session.commit_async(
            keep_recent_count=2,
            memory_policy={
                "self": {"enabled": False},
                "peer": {"enabled": False},
                "working_memory": {"enabled": False},
            },
        )
        task_result = await _wait_for_task(result["task_id"])

        assert task_result["status"] == "completed"
        raw_meta = await session._viking_fs.read_file(
            f"{session._session_uri}/.meta.json",
            ctx=session.ctx,
        )
        saved = json.loads(raw_meta)
        assert saved["commit_count"] == 1
        assert saved["message_count"] == 2
        assert saved["keep_recent_count"] == 2
        assert saved["pending_tokens"] == 0

    async def test_phase2_meta_merge_failure_retries_meta_without_reextracting(
        self, client: AsyncOpenViking
    ):
        """A final meta refresh failure must retry metadata without duplicate side effects."""
        session = client.session(session_id="race_test_phase2_meta_failure_non_terminal")
        archived = Message(id="msg_archived", role="user", parts=[TextPart("archive")])
        task_id = "task_phase2_meta_failure_non_terminal"
        archive_uri = f"{session._session_uri}/history/archive_001"
        await get_task_tracker().create(
            "session_commit",
            resource_id=session.session_id,
            account_id=session.ctx.account_id,
            user_id=session.ctx.user.user_id,
            task_id=task_id,
        )

        attempts = 0
        usage_calls = 0
        original_merge_meta = session._merge_and_save_commit_meta

        async def fail_commit_meta(**_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise LockAcquisitionError("final meta lock busy")
            return await original_merge_meta(**_kwargs)

        async def count_usage_reporting(**_kwargs):
            nonlocal usage_calls
            usage_calls += 1
            return []

        session._merge_and_save_commit_meta = fail_commit_meta
        session._run_usage_reporting = count_usage_reporting

        kwargs = {
            "task_id": task_id,
            "archive_uri": archive_uri,
            "messages": [archived],
            "usage_records": [],
            "first_message_id": archived.id,
            "last_message_id": archived.id,
            "memory_policy": {
                "self": {"enabled": False},
                "peer": {"enabled": False},
                "working_memory": {"enabled": False},
            },
            "retained_message_count": 0,
            "stored_keep_recent_count": 0,
            "record_auto_commit_success": True,
        }

        with pytest.raises(RuntimeError, match="commit meta merge pending"):
            await session._run_memory_extraction(**kwargs)

        task_result = (await get_task_tracker().get(task_id)).to_dict()
        assert task_result["status"] == "running"
        assert usage_calls == 1
        assert await _marker_exists(session, archive_uri, ".extraction_done.json")
        assert not await _marker_exists(session, archive_uri, ".done")
        assert not await _marker_exists(session, archive_uri, ".failed.json")

        await session._run_memory_extraction(**kwargs)

        task_result = (await get_task_tracker().get(task_id)).to_dict()
        assert task_result["status"] == "completed"
        assert usage_calls == 1
        assert await _marker_exists(session, archive_uri, ".done")
        assert not await _marker_exists(session, archive_uri, ".failed.json")

    async def test_stale_auto_commit_policy_snapshot_still_uses_append_lock(
        self, client: AsyncOpenViking
    ):
        """A stale Session must honor a persisted auto-commit policy before appending."""
        ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
        session_id = "race_test_stale_policy_append_lock"
        committing = await client._client._service.sessions.create(ctx, session_id=session_id)
        committing.add_message("user", [TextPart("Original message")])

        stale_appender = client._client._service.sessions.session(ctx, session_id)
        await stale_appender.load()
        assert stale_appender.meta.auto_commit_policy is None

        committing.meta.auto_commit_policy = {"keep_recent_count": 0}
        await committing._save_meta()

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

        with pytest.raises(LockAcquisitionError):
            await asyncio.to_thread(
                stale_appender.add_message,
                "user",
                [TextPart("New message from stale appender")],
            )

        allow_rewrite.set()
        result = await commit_task
        assert result.get("archived") is True
