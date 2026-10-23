# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay driver: SDK-client chunking/ensure, batch append, commit, and crash reconcile."""

from types import SimpleNamespace
from typing import cast

import pytest

from openviking.ingest import poller as poller_module
from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor, NormalizedMessage, SessionRef
from openviking.ingest.poller import IngestPoller
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking.ingest.sources.base import LogSource
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.ingest_config import CommitPolicy, IngestHarnessConfig


class _FakeSDK:
    """Minimal stand-in for ov.AsyncHTTPClient."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.batches = []
        self.commits = []

    async def get_session(self, sid, *, auto_create=False):
        if sid not in self.existing:
            raise NotFoundError(sid, "session")
        return {"session_id": sid, "pending_tokens": 0, "message_count": 0}

    async def create_session(self, session_id=None, options=None):
        self.created.append((session_id, options))
        self.existing.add(session_id)
        return {"session_id": session_id}

    async def batch_add_messages(self, sid, messages, telemetry=False):
        self.batches.append((sid, len(messages)))
        return {"added": len(messages)}

    async def commit_session(self, sid, *, keep_recent_count=0):
        self.commits.append((sid, keep_recent_count))
        return {"task_id": "t", "archived": True}


async def test_ensure_session_get_or_create():
    sdk = _FakeSDK()
    client = ConversationReplayClient(sdk)
    await client.ensure_session("s1")  # not present -> create
    assert sdk.created == [("s1", None)]
    await client.ensure_session("s1")  # now present -> no second create
    assert sdk.created == [("s1", None)]
    # a memory policy travels inside CreateSessionOptions, not as a keyword
    await client.ensure_session("s2", {"working_memory": {"enabled": False}})
    assert sdk.created[-1] == ("s2", {"memory_policy": {"working_memory": {"enabled": False}}})


async def test_append_chunks_at_100():
    sdk = _FakeSDK(existing={"s1"})
    client = ConversationReplayClient(sdk)
    added = await client.append("s1", [{"role": "user", "content": str(i)} for i in range(250)])
    assert added == 250
    assert [n for _, n in sdk.batches] == [100, 100, 50]


class _FakeReplay:
    """Stand-in for ConversationReplayClient used by SessionReplayer."""

    def __init__(self, pending=0, count=0):
        self.pending = pending
        self.count = count
        self.ensured = []
        self.appended = []
        self.committed = []

    async def ensure_session(self, sid, memory_policy=None):
        self.ensured.append(sid)

    async def append(self, sid, payloads):
        self.appended.append((sid, len(payloads)))
        self.count += len(payloads)
        return len(payloads)

    async def message_count(self, sid):
        return self.count

    async def pending_tokens(self, sid):
        return self.pending

    async def commit(self, sid, keep_recent_count=0):
        self.committed.append(sid)

    async def delete(self, sid):
        pass


def _msgs():
    return [
        NormalizedMessage(role="user", text="hi", peer_id="me"),
        NormalizedMessage(role="assistant", text="yo", peer_id="claude_code__m"),
    ]


def _ref(sid="s1"):
    return SessionRef(harness="claude_code", native_session_id=sid, locator="/f")


async def test_append_batch_confirms_cursor_and_marks_needs_commit(tmp_path):
    store = CursorStore(tmp_path)
    fake = _FakeReplay()
    replayer = SessionReplayer(fake, store, session_id_prefix="import")
    ref = _ref()
    added = await replayer.append_batch(
        "claude_code",
        ref,
        _msgs(),
        Cursor(BYTE_OFFSET, {"offset": 0}),
        Cursor(BYTE_OFFSET, {"offset": 99}),
    )
    assert added == 2
    assert fake.ensured == ["import__claude_code__s1"]
    rec = store.get("claude_code", "s1")
    assert rec.cursor.value["offset"] == 99  # confirmed cursor
    assert rec.needs_commit is True
    assert fake.committed == []  # append does not commit


async def test_reconcile_confirms_landed_batch(tmp_path):
    store = CursorStore(tmp_path)
    sid = "import__claude_code__s1"
    store.set_pending(
        "claude_code",
        "s1",
        sid,
        Cursor(BYTE_OFFSET, {"offset": 0}),
        Cursor(BYTE_OFFSET, {"offset": 50}),
        pend_count=2,
        baseline=0,
    )
    fake = _FakeReplay(count=2)  # server already has the 2 messages -> batch landed
    replayer = SessionReplayer(fake, store)
    await replayer.reconcile("claude_code", _ref())
    rec = store.get("claude_code", "s1")
    assert rec.cursor.value["offset"] == 50  # confirmed without re-append
    assert rec.pending_count == 0
    assert rec.needs_commit is True


async def test_reconcile_drops_unlanded_batch(tmp_path):
    store = CursorStore(tmp_path)
    sid = "import__claude_code__s2"
    store.set_pending(
        "claude_code",
        "s2",
        sid,
        Cursor(BYTE_OFFSET, {"offset": 0}),
        Cursor(BYTE_OFFSET, {"offset": 50}),
        pend_count=2,
        baseline=0,
    )
    fake = _FakeReplay(count=0)  # batch did not land
    replayer = SessionReplayer(fake, store)
    await replayer.reconcile("claude_code", _ref("s2"))
    rec = store.get("claude_code", "s2")
    assert rec.cursor.value["offset"] == 0  # NOT advanced -> will re-read & re-append
    assert rec.pending_count == 0  # intent cleared


async def test_threshold_commit(tmp_path):
    store = CursorStore(tmp_path)
    ref = _ref()
    low = SessionReplayer(_FakeReplay(pending=10), store)
    assert await low.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is False
    assert low.client.committed == []

    high = SessionReplayer(_FakeReplay(pending=9000), store)
    assert await high.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is True
    assert high.client.committed == ["import__claude_code__s1"]


async def test_commit_if_needed_skips_when_nothing(tmp_path):
    store = CursorStore(tmp_path)
    replayer = SessionReplayer(_FakeReplay(pending=0), store)
    assert await replayer.commit_if_needed("codex", _ref("s")) is False
    assert replayer.client.committed == []


async def test_commit_if_needed_commits_when_needs_commit_and_pending(tmp_path):
    store = CursorStore(tmp_path)
    # appended-but-uncommitted: needs_commit set in store, server still has pending tokens
    store.set_pending(
        "claude_code",
        "s1",
        "import__claude_code__s1",
        Cursor(BYTE_OFFSET, {"offset": 0}),
        Cursor(BYTE_OFFSET, {"offset": 10}),
        1,
        0,
    )
    store.confirm_append("claude_code", "s1", Cursor(BYTE_OFFSET, {"offset": 10}), 1)
    replayer = SessionReplayer(_FakeReplay(pending=500), store)
    assert await replayer.commit_if_needed("claude_code", _ref()) is True
    assert replayer.client.committed == ["import__claude_code__s1"]
    assert store.get("claude_code", "s1").needs_commit is False


async def test_commit_if_needed_ignores_server_pending_without_local_appends(tmp_path):
    """Regression: server-side pending tokens alone must not trigger a commit.

    The watch sweep calls ``commit_if_needed`` for every session on every tick, and the
    server keeps reporting pending tokens until the queued commit is processed. Treating
    that as "needs commit" re-enqueues the same session on every tick and the queue can
    never catch up.
    """
    store = CursorStore(tmp_path)
    ref = _ref("s1")
    # Known session with live pending tokens server-side, but nothing appended locally.
    store.ensure_row(
        "claude_code", "s1", "import__claude_code__s1", Cursor(BYTE_OFFSET, {"offset": 10})
    )
    fake = _FakeReplay(pending=9000)
    replayer = SessionReplayer(fake, store)

    for _ in range(3):  # three consecutive sweeps
        assert await replayer.commit_if_needed("claude_code", ref) is False
    assert fake.committed == []


async def test_commit_if_needed_still_recovers_after_crash(tmp_path):
    """``needs_commit`` persists, so appended-but-uncommitted work is still flushed."""
    store = CursorStore(tmp_path)
    ref = _ref("s1")
    store.set_pending(
        "claude_code",
        "s1",
        "import__claude_code__s1",
        Cursor(BYTE_OFFSET, {"offset": 0}),
        Cursor(BYTE_OFFSET, {"offset": 10}),
        1,
        0,
    )
    store.confirm_append("claude_code", "s1", Cursor(BYTE_OFFSET, {"offset": 10}), 1)
    fake = _FakeReplay(pending=500)
    # Simulates a restart: a fresh replayer with no in-memory state still commits.
    replayer = SessionReplayer(fake, store)
    assert await replayer.commit_if_needed("claude_code", ref) is True
    assert fake.committed == ["import__claude_code__s1"]


@pytest.mark.parametrize(
    "restarted,new_activity,fail_once,pending,idle,poll,expected_times",
    [
        (False, False, False, 500, 10, 5, [10]),
        (True, False, False, 500, 10, 5, [10]),
        (False, True, False, 500, 10, 5, [15]),
        (False, False, True, 500, 10, 5, [10, 20]),
        (False, False, False, 6000, 10, 5, [0]),
        (False, False, False, 500, 5, 5, [5]),
        (False, False, False, 500, 5, 10, [10]),
    ],
    ids=[
        "idle",
        "restart",
        "new-message",
        "commit-retry",
        "token-threshold",
        "shipped-defaults",
        "poll-longer-than-idle",
    ],
)
async def test_watch_commits_pending_messages(
    tmp_path, monkeypatch, restarted, new_activity, fail_once, pending, idle, poll, expected_times
):
    """Unchanged polls must not defer idle commits, including after a restart."""
    now = 0
    attempts = []
    messages = _msgs()
    ref = _ref()
    store = CursorStore(tmp_path)
    fake = _FakeReplay(pending=pending)
    replayer = SessionReplayer(cast(ConversationReplayClient, fake), store)
    if restarted:
        await replayer.append_batch(
            "claude_code",
            ref,
            messages,
            Cursor.zero(BYTE_OFFSET),
            Cursor(BYTE_OFFSET, {"offset": len(messages)}),
        )
        assert fake.committed == []  # Before restart, messages were appended but not committed.
        replayer = SessionReplayer(cast(ConversationReplayClient, fake), store)

    async def commit(sid, keep_recent_count=0):
        attempts.append(now)
        if fail_once and len(attempts) == 1:
            raise OSError("temporarily unavailable")
        fake.committed.append(sid)

    monkeypatch.setattr(fake, "commit", commit)
    source = SimpleNamespace(
        cursor_kind=BYTE_OFFSET,
        discover_sessions=lambda: [ref],
        read_messages=lambda ref, cursor, limit: (
            messages[(cursor or Cursor.zero(BYTE_OFFSET)).offset :],
            Cursor(BYTE_OFFSET, {"offset": len(messages)}),
        ),
    )
    cfg = IngestHarnessConfig(
        poll_interval_seconds=poll, commit=CommitPolicy(commit_idle_seconds=idle)
    )
    poller = IngestPoller([("claude_code", cfg, cast(LogSource, source))], replayer)

    async def advance(waiter, timeout):
        nonlocal now
        waiter.close()
        now += timeout
        if new_activity and now == 5:
            messages.append(NormalizedMessage(role="user", text="one more message"))
        if now >= 30:
            poller.stop()
        raise TimeoutError

    monkeypatch.setattr(poller_module, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(poller_module.asyncio, "wait_for", advance)
    await poller.run()

    assert attempts == expected_times
    assert fake.committed == ["import__claude_code__s1"]
    record = store.get("claude_code", "s1")
    assert record is not None
    assert record.needs_commit is False
