# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ingest watch commit policy and session isolation."""

from contextlib import closing
from types import SimpleNamespace

import pytest

from openviking.ingest import poller as poller_module
from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.models import BYTE_OFFSET, Cursor
from openviking.ingest.poller import IngestPoller
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking.ingest.sources.hermes import HermesSource
from openviking_cli.utils.config.ingest_config import IngestHarnessConfig
from tests.ingest.test_replay import _FakeReplay, _FakeSDK


async def test_tick_keeps_polling_other_sessions_when_one_fails(tmp_path, monkeypatch):
    for name in ("bad", "good"):
        (tmp_path / f"{name}.jsonl").write_text(
            f'{{"role": "user", "content": "{name} message"}}\n'
        )
    config = IngestHarnessConfig(enabled=True, paths=[str(tmp_path)])
    source = HermesSource(config, fallback_user="tester")
    sdk = _FakeSDK()
    append = sdk.batch_add_messages
    failures = {"import__hermes__bad", "import__hermes__good"}
    attempted = []

    async def flaky_append(session_id, messages):
        attempted.append(session_id)
        if session_id in failures:
            raise ConnectionError("session backend unavailable")
        return await append(session_id, messages)

    monkeypatch.setattr(sdk, "batch_add_messages", flaky_append)
    with closing(CursorStore(tmp_path / "state")) as store:
        replayer = SessionReplayer(ConversationReplayClient(sdk), store)
        poller = IngestPoller([("hermes", config, source)], replayer)
        await poller._tick()
        assert set(attempted) == failures
        assert sdk.batches == []
        for name in ("bad", "good"):
            item = store.get("hermes", name)
            assert item is not None
            assert item.cursor.offset == 0
            assert item.pending_count == 1
            assert not item.needs_commit

        failures.remove("import__hermes__good")
        await poller._tick()
        [(session_id, messages)] = sdk.batches
        assert session_id == "import__hermes__good"
        assert [message["parts"][0]["text"] for message in messages] == ["good message"]
        good = store.get("hermes", "good")
        bad = store.get("hermes", "bad")
        assert good is not None and bad is not None
        assert good.cursor.offset == (tmp_path / "good.jsonl").stat().st_size
        assert good.pending_count == 0
        assert good.needs_commit
        assert bad.cursor.offset == 0
        assert bad.pending_count == 1


@pytest.mark.parametrize("batch_state", ["new", "confirmed", "unacknowledged", "absent"])
async def test_watch_shutdown_after_read_error(tmp_path, monkeypatch, batch_state):
    (tmp_path / "s1.jsonl").write_text('{"role": "user", "content": "hello"}\n')
    config = IngestHarnessConfig(enabled=True, paths=[str(tmp_path)])
    source = HermesSource(config, fallback_user="test")
    ref = next(iter(source.discover_sessions()))
    messages, confirmed_cursor = source.read_messages(ref, None)
    state_dir = tmp_path / "state"
    fake = _FakeReplay(pending=1)
    sid = "import__hermes__s1"
    if batch_state in {"confirmed", "unacknowledged"}:
        with closing(CursorStore(state_dir)) as store:
            store.set_pending("hermes", "s1", sid, Cursor.zero(BYTE_OFFSET), confirmed_cursor, 1, 0)
            fake.count = 1  # The remote service accepted the batch before restart.
            if batch_state == "confirmed":
                store.confirm_append("hermes", "s1", confirmed_cursor, 1)

    with closing(CursorStore(state_dir)) as store:
        poller = IngestPoller([("hermes", config, source)], SessionReplayer(fake, store))

        def read_until_error(_ref, cursor, _limit):
            if batch_state == "new" and cursor is None:
                return messages, confirmed_cursor
            poller.stop()
            raise OSError("source unavailable")

        monkeypatch.setattr(source, "read_messages", read_until_error)
        await poller.run()

        record = store.get("hermes", ref.native_session_id)
        if batch_state != "absent":
            assert record is not None
            assert record.cursor == confirmed_cursor
            assert fake.committed == [sid]
            assert fake.appended == ([(sid, 1)] if batch_state == "new" else [])
            assert record.pending_count == 0
            assert not record.needs_commit
        else:
            assert record is None
            assert fake.committed == []


@pytest.mark.parametrize("pending_tokens", [1, 6000], ids=["idle-deadline", "token-threshold"])
async def test_watch_read_errors_preserve_commit_policy(tmp_path, monkeypatch, pending_tokens):
    (tmp_path / "s1.jsonl").write_text('{"role": "user", "content": "hello"}\n')
    config = IngestHarnessConfig(enabled=True, paths=[str(tmp_path)])
    source = HermesSource(config, fallback_user="test")
    ref = next(iter(source.discover_sessions()))
    messages, cursor = source.read_messages(ref, None)
    fake = _FakeReplay(pending=pending_tokens)
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(poller_module, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def fail_read(*_args):
        raise OSError("source unavailable")

    monkeypatch.setattr(source, "read_messages", fail_read)
    with closing(CursorStore(tmp_path / "state")) as store:
        replayer = SessionReplayer(fake, store)
        await replayer.append_batch("hermes", ref, messages, Cursor.zero(BYTE_OFFSET), cursor)
        poller = IngestPoller([("hermes", config, source)], replayer)
        sid = "import__hermes__s1"
        await poller._tick()
        assert fake.committed == ([sid] if pending_tokens == 6000 else [])

        clock.now = config.commit.commit_idle_seconds
        await poller._tick()
        await poller._commit_idle()
        assert fake.committed == [sid]
        assert not store.get("hermes", "s1").needs_commit
