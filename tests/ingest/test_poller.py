# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One failing session must not stop ingest for every other session."""

from __future__ import annotations

from types import SimpleNamespace

from openviking.ingest.models import BYTE_OFFSET, Cursor, NormalizedMessage, SessionRef
from openviking.ingest.poller import IngestPoller


class _Source:
    cursor_kind = BYTE_OFFSET

    def __init__(self, refs):
        self.refs = refs
        self.reads = []

    def discover_sessions(self):
        return list(self.refs)

    def read_messages(self, ref, cursor, limit):
        self.reads.append(ref.native_session_id)
        offset = cursor.offset if cursor else 0
        # One message on the first read, then EOF so _poll_session terminates.
        if self.reads.count(ref.native_session_id) == 1:
            message = NormalizedMessage(role="user", text="hello")
            return [message], Cursor(BYTE_OFFSET, {"offset": offset + 1})
        return [], Cursor(BYTE_OFFSET, {"offset": offset})


class _Store:
    def __init__(self):
        self.cursors = {}

    def get_cursor(self, harness, native_session_id, cursor_kind):
        return self.cursors.get((harness, native_session_id))

    def get(self, harness, native_session_id):
        return None


class _Replayer:
    def __init__(self, failing):
        self.failing = failing
        self.attempted = []
        self.appended = []
        self.store = _Store()

    def ov_session_id(self, harness, native_session_id):
        return f"import__{harness}__{native_session_id}"

    async def reconcile(self, harness, ref):
        return True

    async def append_batch(self, harness, ref, messages, from_cursor, new_cursor):
        self.attempted.append(ref.native_session_id)
        if ref.native_session_id in self.failing:
            raise RuntimeError("session backend unavailable")
        self.appended.append(ref.native_session_id)
        return len(messages)

    async def maybe_commit_on_threshold(self, harness, ref, threshold, keep_recent_count=0):
        return False


def _config():
    return SimpleNamespace(
        poll_interval_seconds=5.0,
        commit=SimpleNamespace(commit_token_threshold=6000, keep_recent_count=0),
    )


def _ref(native_session_id):
    return SessionRef(
        harness="hermes",
        native_session_id=native_session_id,
        locator=f"/tmp/{native_session_id}.jsonl",
    )


async def test_tick_keeps_polling_other_sessions_when_one_fails():
    source = _Source([_ref("bad"), _ref("good")])
    replayer = _Replayer(failing={"bad"})
    poller = IngestPoller([("hermes", _config(), source)], replayer)

    await poller._tick()

    assert replayer.attempted == ["bad", "good"]
    assert replayer.appended == ["good"]
    assert ("hermes", "good") in poller._dirty


async def test_tick_returns_when_every_session_fails():
    source = _Source([_ref("bad")])
    replayer = _Replayer(failing={"bad"})
    poller = IngestPoller([("hermes", _config(), source)], replayer)

    await poller._tick()

    assert replayer.attempted == ["bad"]
    assert poller._dirty == {}
