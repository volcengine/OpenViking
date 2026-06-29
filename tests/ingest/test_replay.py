# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay driver: SDK-client chunking/ensure, and SessionReplayer commit decisions."""

from openviking_cli.exceptions import NotFoundError

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor, NormalizedMessage, SessionRef
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer


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
        return {"session_id": sid, "pending_tokens": 0}

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

    def __init__(self, pending=0):
        self.pending = pending
        self.ensured = []
        self.appended = []
        self.committed = []

    async def ensure_session(self, sid, memory_policy=None):
        self.ensured.append(sid)

    async def append(self, sid, payloads):
        self.appended.append((sid, len(payloads)))
        return len(payloads)

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


async def test_append_persists_cursor_before_any_commit(tmp_path):
    store = CursorStore(tmp_path)
    fake = _FakeReplay(pending=10)
    replayer = SessionReplayer(fake, store, session_id_prefix="import")
    ref = SessionRef(harness="claude_code", native_session_id="s1", locator="/f")

    cur = Cursor(BYTE_OFFSET, {"offset": 99})
    added = await replayer.append_session("claude_code", ref, _msgs(), cur)

    assert added == 2
    assert fake.ensured == ["import__claude_code__s1"]
    # cursor persisted by append (pre-commit); no commit happened in append_session
    rec = store.get("claude_code", "s1")
    assert rec.cursor.value["offset"] == 99
    assert fake.committed == []
    store.close()


async def test_threshold_commit(tmp_path):
    store = CursorStore(tmp_path)
    ref = SessionRef(harness="claude_code", native_session_id="s1", locator="/f")

    low = SessionReplayer(_FakeReplay(pending=10), store)
    assert await low.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is False
    assert low.client.committed == []

    store.upsert("claude_code", "s1", "import__claude_code__s1", Cursor(BYTE_OFFSET, {"offset": 1}))
    high = SessionReplayer(_FakeReplay(pending=9000), store)
    assert await high.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is True
    assert high.client.committed == ["import__claude_code__s1"]
    store.close()


async def test_commit_session_skips_when_no_pending(tmp_path):
    store = CursorStore(tmp_path)
    ref = SessionRef(harness="codex", native_session_id="s", locator="/f")
    replayer = SessionReplayer(_FakeReplay(pending=0), store)
    assert await replayer.commit_session("codex", ref) is False
    assert replayer.client.committed == []
    store.close()
