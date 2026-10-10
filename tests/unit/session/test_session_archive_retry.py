# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from openviking.message import Message, TextPart
from openviking.service.task_tracker import TaskStatus
from openviking.session.archive_store import ArchiveState, ArchiveStore
from openviking.session.session import Session
from openviking.storage.queuefs.session_commit_msg import SessionCommitMsg
from openviking_cli.exceptions import FailedPreconditionError, NotFoundError

SESSION_URI = "viking://user/alice/sessions/session"
ARCHIVE_URI = f"{SESSION_URI}/history/archive_001"


class _RecoveryFS:
    def __init__(self):
        self.files = {}
        self._async_agfs = SimpleNamespace(
            pathlock_acquire_tree=AsyncMock(return_value={"lease_ref": "lease"}),
            pathlock_release=AsyncMock(),
        )

    def _uri_to_path(self, uri, **kwargs):
        return uri

    async def read_file(self, uri, **kwargs):
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        return self.files[uri]

    async def write_file(self, uri, content, **kwargs):
        self.files[uri] = content

    async def exists(self, uri, **kwargs):
        return uri in self.files

    async def rm(self, uri, **kwargs):
        if uri not in self.files:
            raise NotFoundError(uri, "file")
        del self.files[uri]


def _commit_message(task_id="task-1"):
    return SessionCommitMsg(
        task_id=task_id,
        session_id="session",
        session_uri=SESSION_URI,
        archive_uri=ARCHIVE_URI,
        user={"account_id": "default", "user_id": "alice"},
    )


class _Env:
    def __init__(self):
        self.fs = _RecoveryFS()
        message = Message(id="message-1", role="user", parts=[TextPart(text="preserve me")])
        raw = json.dumps(message.to_dict(), ensure_ascii=False) + "\n"
        self.messages_sha256 = hashlib.sha256(raw.encode()).hexdigest()
        self.phase1_meta = {
            "phase1": {"status": "ready", "queue_message": _commit_message().to_dict()}
        }
        self.fs.files[f"{ARCHIVE_URI}/messages.jsonl"] = raw
        self.fs.files[f"{ARCHIVE_URI}/.meta.json"] = json.dumps(self.phase1_meta)

        session = object.__new__(Session)
        session._viking_fs = self.fs
        session._archive_meta_merge_lock = asyncio.Lock()
        session._session_uri = SESSION_URI
        session.session_id = "session"
        session._archives = ArchiveStore(
            self.fs, SimpleNamespace(account_id="default"), SESSION_URI
        )
        session.ctx = SimpleNamespace(
            account_id="default",
            user=SimpleNamespace(
                user_id="alice", to_dict=lambda: {"account_id": "default", "user_id": "alice"}
            ),
        )
        self.session = session
        self.tracker = SimpleNamespace(
            MAX_TASKS=10_000,
            list_tasks=AsyncMock(return_value=[]),
            get=AsyncMock(return_value=None),
            has_work=Mock(return_value=False),
            fail=AsyncMock(),
            create=AsyncMock(return_value=SimpleNamespace(status=TaskStatus.PENDING)),
            complete=AsyncMock(),
        )
        self.queue = SimpleNamespace(snapshot=AsyncMock(return_value=[]))
        self.queue_manager = SimpleNamespace(
            get_queue=Mock(return_value=self.queue), enqueue=AsyncMock()
        )

    @staticmethod
    def state(state="pending", failed=None):
        return ArchiveState(
            archive_id="archive_001",
            archive_uri=ARCHIVE_URI,
            index=1,
            state=state,
            failed=failed or {},
        )

    async def retry(self, state, **kwargs):
        self.session._archives.scan_states = AsyncMock(return_value=[state])
        kwargs.setdefault("expected_messages_sha256", self.messages_sha256)
        with (
            patch("openviking.service.task_tracker.get_task_tracker", return_value=self.tracker),
            patch("openviking.storage.queuefs.get_queue_manager", return_value=self.queue_manager),
        ):
            return await self.session.retry_archive("archive_001", **kwargs)

    def recovery_message(self, task_id="recovery-task"):
        msg = _commit_message(task_id)
        msg.recovery = {
            "version": 1,
            "kind": "failed",
            "task_id": task_id,
            "previous_task_id": "task-1",
            "messages_sha256": self.messages_sha256,
        }
        return msg

    def assert_nothing_enqueued(self):
        self.tracker.create.assert_not_awaited()
        self.queue_manager.enqueue.assert_not_awaited()


@pytest.fixture
def env():
    return _Env()


def test_recovery_field_is_backward_compatible():
    legacy = _commit_message().to_dict()
    legacy.pop("recovery")
    assert SessionCommitMsg.from_dict(legacy).recovery == {}
    recovery = {"kind": "failed", "messages_sha256": "0" * 64}
    restored = SessionCommitMsg.from_dict({**legacy, "recovery": recovery})
    assert restored.to_dict()["recovery"] == recovery


async def test_ownerless_ready_requires_explicit_opt_in(env):
    before = dict(env.fs.files)
    with pytest.raises(FailedPreconditionError, match="allow_ownerless_ready"):
        await env.retry(env.state())
    assert env.fs.files == before
    env.assert_nothing_enqueued()


async def test_hash_mismatch_has_no_side_effects(env):
    before = dict(env.fs.files)
    with pytest.raises(FailedPreconditionError, match="messages changed"):
        await env.retry(env.state(), expected_messages_sha256="0" * 64, allow_ownerless_ready=True)
    assert env.fs.files == before
    env.assert_nothing_enqueued()


async def test_completed_archive_cannot_be_retried(env):
    env.fs.files[f"{ARCHIVE_URI}/.done"] = "{}"
    with pytest.raises(FailedPreconditionError, match="Completed archives"):
        await env.retry(env.state(), allow_ownerless_ready=True)
    env.assert_nothing_enqueued()


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"archive_uri": ARCHIVE_URI, "task_id": "archive-owner"}, "archive_owned"),
        (
            {"session_uri": SESSION_URI, "archive_uri": ARCHIVE_URI + "-other", "task_id": "x"},
            "session_busy",
        ),
    ],
)
async def test_queued_owner_skips_recovery(env, payload, reason):
    env.queue.snapshot.return_value = [{"data": json.dumps(payload)}]
    result = await env.retry(env.state(), allow_ownerless_ready=True)
    assert (result["status"], result["reason"]) == ("skipped", reason)
    env.assert_nothing_enqueued()


async def test_active_worker_skips_recovery(env):
    env.tracker.list_tasks.return_value = [SimpleNamespace(task_id="live")]
    env.tracker.has_work.return_value = True
    result = await env.retry(env.state(), allow_ownerless_ready=True)
    assert (result["reason"], result["task_id"]) == ("session_busy", "live")
    env.assert_nothing_enqueued()


async def test_cancelled_failure_requires_explicit_opt_in(env):
    failure = {"stage": "cancelled", "error": "session commit cancelled"}
    failed_raw = json.dumps(failure)
    env.fs.files[f"{ARCHIVE_URI}/.failed.json"] = failed_raw

    with pytest.raises(FailedPreconditionError, match="explicit review"):
        await env.retry(env.state("failed", failure))
    env.assert_nothing_enqueued()

    with patch("openviking.session.session.uuid4", return_value="cancel-recovery"):
        result = await env.retry(env.state("failed", failure), allow_cancelled_failure=True)
    assert result["status"] == "accepted"
    assert env.fs.files[f"{ARCHIVE_URI}/.failure-before-cancel-recovery.json"] == failed_raw


async def test_memory_diff_without_progress_is_rejected(env):
    failure = {"stage": "memory", "error": "timeout"}
    env.fs.files[f"{ARCHIVE_URI}/.failed.json"] = json.dumps(failure)
    env.fs.files[f"{ARCHIVE_URI}/memory_diff.json"] = "{}"
    with pytest.raises(FailedPreconditionError, match="memory_diff.json"):
        await env.retry(env.state("failed", failure))
    env.assert_nothing_enqueued()


async def test_failed_archive_is_enqueued_with_bound_recovery(env):
    failure = {"stage": "working_memory", "error": "context limit"}
    failed_raw = json.dumps(failure)
    env.fs.files[f"{ARCHIVE_URI}/.failed.json"] = failed_raw
    env.fs.files[f"{ARCHIVE_URI}/memory_diff.json"] = "{}"
    env.fs.files[f"{ARCHIVE_URI}/.meta.json"] = json.dumps(
        {**env.phase1_meta, "completed_memory_steps": {"long_term": ["message-1"]}}
    )

    with patch("openviking.session.session.uuid4", return_value="recovery-task"):
        result = await env.retry(env.state("failed", failure))

    assert result["status"] == "accepted"
    assert result["recovery_kind"] == "failed"
    assert result["completed_memory_steps"] == {"long_term": ["message-1"]}
    saved = json.loads(env.fs.files[f"{ARCHIVE_URI}/.meta.json"])
    assert saved["recovery"]["task_id"] == "recovery-task"
    assert saved["recovery"]["failed_sha256"] == hashlib.sha256(failed_raw.encode()).hexdigest()
    assert env.fs.files[f"{ARCHIVE_URI}/.failure-before-recovery-task.json"] == failed_raw
    enqueued = env.queue_manager.enqueue.await_args.args[1]
    assert enqueued["recovery"]["task_id"] == "recovery-task"
    assert enqueued["recovery"]["messages_sha256"] == env.messages_sha256


async def test_activation_removes_matching_failure_marker(env):
    msg = env.recovery_message()
    failed_raw = json.dumps({"stage": "working_memory", "error": "timeout"})
    env.fs.files[f"{ARCHIVE_URI}/.failed.json"] = failed_raw
    meta = {
        **env.phase1_meta,
        "recovery": {
            **msg.recovery,
            "failed_sha256": hashlib.sha256(failed_raw.encode()).hexdigest(),
        },
    }
    env.fs.files[f"{ARCHIVE_URI}/.meta.json"] = json.dumps(meta)
    await env.session._activate_archive_recovery(msg)
    assert f"{ARCHIVE_URI}/.failed.json" not in env.fs.files


async def test_consumer_consumes_ownership_mismatch_as_failure(env):
    msg = env.recovery_message()
    meta = {**env.phase1_meta, "recovery": {**msg.recovery, "task_id": "another-task"}}
    env.fs.files[f"{ARCHIVE_URI}/.meta.json"] = json.dumps(meta)
    with patch("openviking.service.task_tracker.get_task_tracker", return_value=env.tracker):
        assert await env.session.resume_queued_commit(msg) is True
    env.tracker.fail.assert_awaited_once()
    assert "does not own" in env.tracker.fail.await_args.args[1]


async def test_redelivery_requires_matching_failure_backup(env):
    msg = env.recovery_message()
    failed_raw = json.dumps({"stage": "working_memory", "error": "timeout"})
    meta = {
        **env.phase1_meta,
        "recovery": {
            **msg.recovery,
            "failed_sha256": hashlib.sha256(failed_raw.encode()).hexdigest(),
        },
    }
    env.fs.files[f"{ARCHIVE_URI}/.meta.json"] = json.dumps(meta)

    with pytest.raises(FailedPreconditionError, match="without a recovery backup"):
        await env.session._activate_archive_recovery(msg)

    backup = f"{ARCHIVE_URI}/.failure-before-{msg.task_id}.json"
    env.fs.files[backup] = failed_raw
    await env.session._activate_archive_recovery(msg)
    env.fs.files[backup] = failed_raw + "changed"
    with pytest.raises(FailedPreconditionError, match="backup changed"):
        await env.session._activate_archive_recovery(msg)


async def test_transient_storage_failure_is_not_acknowledged(env):
    msg = env.recovery_message()
    env.session._activate_archive_recovery = AsyncMock(side_effect=OSError("temporary"))
    with patch("openviking.service.task_tracker.get_task_tracker", return_value=env.tracker):
        with pytest.raises(OSError, match="temporary"):
            await env.session.resume_queued_commit(msg)
    env.tracker.fail.assert_not_awaited()
