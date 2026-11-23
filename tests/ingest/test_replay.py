# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Replay driver: SDK-client chunking/ensure, batch append, commit, and crash reconcile."""

from contextlib import closing
from unittest.mock import patch

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor, NormalizedMessage, SessionRef
from openviking.ingest.orchestrator import IngestOrchestrator
from openviking.ingest.poller import IngestPoller
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking.ingest.sources.hermes import HermesSource
from openviking_cli.exceptions import NotFoundError
from openviking_cli.utils.config.ingest_config import IngestConfig, IngestHarnessConfig


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
        count = sum(len(messages) for session_id, messages in self.batches if session_id == sid)
        return {"session_id": sid, "pending_tokens": count, "message_count": count}

    async def create_session(self, session_id=None, options=None):
        self.created.append((session_id, options))
        self.existing.add(session_id)
        return {"session_id": session_id}

    async def batch_add_messages(self, sid, messages, telemetry=False):
        self.batches.append((sid, messages))
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
    messages = [{"role": "user", "content": str(i)} for i in range(250)]
    assert await client.append("s1", messages) == len(messages)
    assert sdk.batches == [
        ("s1", messages[:100]),
        ("s1", messages[100:200]),
        ("s1", messages[200:]),
    ]


class _FakeReplay(ConversationReplayClient):
    """Stand-in for ConversationReplayClient used by SessionReplayer."""

    def __init__(self, pending=0, count=0):
        self.pending = pending
        self.count = count
        self.ensured = []
        self.appended = []
        self.committed = []

    async def ensure_session(self, sid, memory_policy=None):
        self.ensured.append(sid)

    async def append(self, sid, messages):
        self.appended.append((sid, len(messages)))
        self.count += len(messages)
        return len(messages)

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
    fake = _FakeReplay(pending=500)
    ref = _ref()
    cursor = Cursor(BYTE_OFFSET, {"offset": 99})
    with closing(CursorStore(tmp_path)) as store:
        replayer = SessionReplayer(fake, store)
        added = await replayer.append_batch(
            "claude_code", ref, _msgs(), Cursor.zero(BYTE_OFFSET), cursor
        )
        assert added == 2
        assert fake.ensured == ["import__claude_code__s1"]
        assert fake.appended == [("import__claude_code__s1", 2)]
        assert fake.committed == []

    # The next process flushes the confirmed messages, without re-appending
    # them or issuing another commit while the server still reports pending tokens.
    with closing(CursorStore(tmp_path)) as store:
        assert store.get_cursor("claude_code", "s1", BYTE_OFFSET) == cursor
        replayer = SessionReplayer(fake, store)
        assert await replayer.commit_if_needed("claude_code", ref)
        assert not await replayer.commit_if_needed("claude_code", ref)
        assert fake.committed == ["import__claude_code__s1"]
        assert fake.appended == [("import__claude_code__s1", 2)]


async def test_reconcile_defers_replay_then_confirms_landed_batch(tmp_path):
    log = tmp_path / "s1.jsonl"
    log.write_text('{"role": "user", "content": "hello"}\n')
    config = IngestHarnessConfig(enabled=True, paths=[str(tmp_path)])
    source = HermesSource(config, fallback_user="test")
    sdk = _FakeSDK()
    append = sdk.batch_add_messages

    async def append_then_timeout(session_id, messages):
        await append(session_id, messages)
        raise TimeoutError("append response lost")

    state = tmp_path / "state"
    with closing(CursorStore(state)) as store:
        replayer = SessionReplayer(ConversationReplayClient(sdk), store)
        with patch.object(sdk, "batch_add_messages", side_effect=append_then_timeout):
            stats = await IngestOrchestrator(IngestConfig(), replayer).backfill_source(
                "hermes", config, source
            )
        assert stats.errors == ["hermes/s1: append response lost"]
        pending = store.get("hermes", "s1")
        assert pending is not None
        assert pending.cursor.offset == 0
        assert pending.pending_count == len(sdk.batches) == 1

    with closing(CursorStore(state)) as store:
        replayer = SessionReplayer(ConversationReplayClient(sdk), store)
        orchestrator = IngestOrchestrator(IngestConfig(), replayer)
        poller = IngestPoller([("hermes", config, source)], replayer)
        with patch.object(
            sdk, "get_session", side_effect=OSError("temporary session read failure")
        ):
            stats = await orchestrator.backfill_source("hermes", config, source)
            assert stats.sessions == 0
            assert stats.errors == ["hermes/s1: pending batch reconciliation deferred"]
            await poller._tick()
            assert store.get("hermes", "s1") == pending
            assert len(sdk.batches) == 1

        await poller._tick()
        confirmed = store.get("hermes", "s1")
        assert confirmed is not None
        assert confirmed.cursor.offset == log.stat().st_size
        assert confirmed.pending_count == 0
        assert confirmed.needs_commit

        stats = await orchestrator.backfill_source("hermes", config, source)
        assert (stats.sessions, stats.messages, stats.committed, stats.errors) == (1, 0, 1, [])
        assert len(sdk.batches) == 1
        assert sdk.commits == [("import__hermes__s1", 0)]


async def test_reconcile_drops_unlanded_batch(tmp_path):
    log = tmp_path / "s1.jsonl"
    log.write_text('{"role": "user", "content": "retry this message"}\n')
    config = IngestHarnessConfig(enabled=True, paths=[str(tmp_path)])
    source = HermesSource(config, fallback_user="test")
    sdk = _FakeSDK()
    state = tmp_path / "state"

    with closing(CursorStore(state)) as store:
        replayer = SessionReplayer(ConversationReplayClient(sdk), store)
        with patch.object(
            sdk, "batch_add_messages", side_effect=TimeoutError("append did not land")
        ):
            stats = await IngestOrchestrator(IngestConfig(), replayer).backfill_source(
                "hermes", config, source
            )
        assert stats.errors == ["hermes/s1: append did not land"]
        assert sdk.batches == []
        pending = store.get("hermes", "s1")
        assert pending is not None and pending.pending_count == 1
        assert pending.cursor.offset == 0

    with closing(CursorStore(state)) as store:
        replayer = SessionReplayer(ConversationReplayClient(sdk), store)
        stats = await IngestOrchestrator(IngestConfig(), replayer).backfill_source(
            "hermes", config, source
        )
        assert (stats.sessions, stats.messages, stats.committed, stats.errors) == (1, 1, 1, [])
        [(session_id, messages)] = sdk.batches
        assert session_id == "import__hermes__s1"
        assert [message["content"] for message in messages] == ["retry this message"]
        confirmed = store.get("hermes", "s1")
        assert confirmed is not None
        assert confirmed.cursor.offset == log.stat().st_size
        assert confirmed.pending_count == 0


async def test_threshold_commit(tmp_path):
    store = CursorStore(tmp_path)
    ref = _ref()
    low_client = _FakeReplay(pending=10)
    low = SessionReplayer(low_client, store)
    assert await low.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is False
    assert low_client.committed == []

    high_client = _FakeReplay(pending=9000)
    high = SessionReplayer(high_client, store)
    assert await high.maybe_commit_on_threshold("claude_code", ref, threshold=6000) is True
    assert high_client.committed == ["import__claude_code__s1"]


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
