# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A failed session append must leave other logs free to advance."""

from contextlib import closing

from openviking.ingest.cursor_store import CursorStore
from openviking.ingest.poller import IngestPoller
from openviking.ingest.replay import ConversationReplayClient, SessionReplayer
from openviking.ingest.sources.hermes import HermesSource
from openviking_cli.utils.config.ingest_config import IngestHarnessConfig
from tests.ingest.test_replay import _FakeSDK


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
