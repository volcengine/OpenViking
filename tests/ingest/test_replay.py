# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay driver: SDK-client chunking/ensure, batch append, commit, and crash reconcile."""

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor, NormalizedMessage, SessionRef
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking_cli.exceptions import NotFoundError


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

    async def create_session(self, session_id=None, memory_policy=None):
        self.created.append(session_id)
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
    assert sdk.created == ["s1"]
    await client.ensure_session("s1")  # now present -> no second create
    assert sdk.created == ["s1"]


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
