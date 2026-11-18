"""Durable append recovery across every publication boundary and lost replies."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.session import Session, SessionMeta
from openviking_cli.exceptions import ConflictError
from openviking_cli.session.user_id import UserIdentifier


class Storage:
    def __init__(self):
        self.files = {}
        self.mutex = asyncio.Lock()
        self._async_agfs = SimpleNamespace(
            pathlock_acquire_exact=self.acquire,
            pathlock_acquire_tree=self.acquire,
            pathlock_acquire_exact_batch=self.acquire_batch,
            pathlock_acquire_batch=self.acquire_mixed,
            pathlock_release=self.release,
        )
        self.fail_after = None
        self.writes = 0
        self.torn = False

    def _uri_to_path(self, uri, **kwargs):
        return uri

    async def acquire(self, path, **kwargs):
        await self.mutex.acquire()
        return path

    async def acquire_batch(self, paths, *, owner_lease_ref=None, **kwargs):
        assert owner_lease_ref is not None and self.mutex.locked()
        return tuple(paths)

    async def acquire_mixed(self, requests, **kwargs):
        return await self.acquire_batch([row["path"] for row in requests], **kwargs)

    async def release(self, lease):
        if not isinstance(lease, tuple):
            self.mutex.release()

    async def read_file(self, uri, **kwargs):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def read_file_bytes(self, uri, **kwargs):
        return (await self.read_file(uri, **kwargs)).encode()

    async def write_file(self, uri, content, **kwargs):
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.writes += 1
        if self.torn and uri.endswith("messages.jsonl"):
            self.files[uri] = content[: len(content) // 2]
            self.torn = False
            raise RuntimeError("process stopped during write")
        self.files[uri] = content
        if self.writes == self.fail_after:
            raise RuntimeError("reply lost after write")

    async def append_file(self, uri, content, **kwargs):
        self.files[uri] += content

    async def mkdir(self, *args, **kwargs):
        pass

    async def rm(self, uri, **kwargs):
        self.files.pop(uri, None)


CTX = RequestContext(user=UserIdentifier("tenant", "alice"), role=Role.USER)
URI = "viking://user/alice/sessions/recovery"


def setup():
    fs = Storage()
    seed = Message(
        id="seed", role="user", parts=[TextPart("seed")], created_at="2026-10-12T00:00:00Z"
    )
    fs.files[URI + "/messages.jsonl"] = seed.to_jsonl() + "\n"
    fs.files[URI + "/.meta.json"] = json.dumps(
        SessionMeta(session_id="recovery", message_count=1, total_message_count=1).to_dict()
    )
    return fs


def session(fs):
    return Session(viking_fs=fs, session_id="recovery", session_uri=URI, ctx=CTX)


def specs(content="new"):
    return [{"role": "user", "parts": [TextPart(content)]}]


@pytest.mark.parametrize("boundary", range(1, 6))
async def test_lost_write_reply_and_restart_preserve_exactly_one_append(boundary):
    fs = setup()
    fs.fail_after = boundary
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs(), "batch")
    fs.fail_after = None
    restarted = session(fs)
    await restarted.load()
    result = await restarted.add_messages_idempotent_async(specs(), "batch")
    replay = await session(fs).add_messages_idempotent_async(specs(), "batch")
    assert replay.replayed
    assert replay.result == result.result
    rows = [json.loads(line) for line in fs.files[URI + "/messages.jsonl"].splitlines()]
    assert len(rows) == len({row["id"] for row in rows}) == 2
    assert json.loads(fs.files[URI + "/.meta.json"])["total_message_count"] == 2
    assert URI + "/.append_receipts/pending.json" not in fs.files


async def test_torn_write_is_repaired_before_loading_messages():
    fs = setup()
    fs.torn = True
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs("新消息"), "torn")
    restarted = session(fs)
    await restarted.load()
    result = await restarted.add_messages_idempotent_async(specs("新消息"), "torn")
    assert result.replayed
    assert [m.content for m in restarted.messages] == ["seed", "新消息"]


async def test_same_key_different_body_is_conflict():
    fs = setup()
    await session(fs).add_messages_idempotent_async(specs(), "key")
    with pytest.raises(ConflictError):
        await session(fs).add_messages_idempotent_async(specs("different"), "key")
    assert len(fs.files[URI + "/messages.jsonl"].splitlines()) == 2


async def test_concurrent_sessions_serialize_and_deduplicate():
    fs = setup()
    await asyncio.gather(
        *[
            session(fs).add_messages_idempotent_async(specs(str(i)), f"key-{i}")
            for i in list(range(20)) + list(range(20))
        ]
    )
    rows = [json.loads(line) for line in fs.files[URI + "/messages.jsonl"].splitlines()]
    assert len(rows) == len({r["id"] for r in rows}) == 21
    assert json.loads(fs.files[URI + "/.meta.json"])["total_message_count"] == 21


async def test_legacy_append_recovers_pending_intent_first():
    fs = setup()
    fs.fail_after = 2
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs(), "pending")
    fs.fail_after = None
    await session(fs).add_messages_async(specs("legacy"))
    rows = [json.loads(line) for line in fs.files[URI + "/messages.jsonl"].splitlines()]
    assert len(rows) == 3
    assert [row["parts"][0]["text"] for row in rows] == ["seed", "new", "legacy"]


async def test_recovery_does_not_overwrite_unrelated_changes():
    fs = setup()
    fs.fail_after = 2
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs(), "pending")
    fs.fail_after = None
    fs.files[URI + "/messages.jsonl"] = "unexpected external change"
    with pytest.raises(ConflictError):
        await session(fs).load()
    assert fs.files[URI + "/messages.jsonl"] == "unexpected external change"


async def test_cancelled_lock_acquisition_releases_its_eventual_native_lease():
    fs = setup()
    started, finish = asyncio.Event(), asyncio.Event()
    write = fs.write_file

    async def paused_write(uri, content, **kwargs):
        if uri.endswith("pending.json"):
            started.set()
            await finish.wait()
        return await write(uri, content, **kwargs)

    fs.write_file = paused_write
    task = asyncio.create_task(session(fs).add_messages_idempotent_async(specs(), "cancelled"))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    assert not task.done() and fs.mutex.locked()
    await asyncio.sleep(0)
    task.cancel()
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fs.mutex.locked()
    replay = await session(fs).add_messages_idempotent_async(specs(), "cancelled")
    assert replay.replayed and replay.result["message_count"] == 2


@pytest.mark.parametrize("file", ["messages.jsonl", ".meta.json", "completed"])
async def test_torn_root_and_completed_receipts_recover_from_valid_intent(file):
    fs = setup()
    # Stop immediately after a complete durable intent, before publishing root.
    fs.fail_after = 2
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs(), "torn-root")
    fs.fail_after = None
    pending = json.loads(fs.files[URI + "/.append_receipts/pending.json"])
    if file == "completed":
        path = URI + "/.append_receipts/" + pending["key_digest"] + ".json"
        fs.files[path] = '{"version":'
    elif file == "messages.jsonl":
        # Truncate inside the old prefix, not only the newly appended suffix.
        fs.files[URI + "/" + file] = pending["prefix"][:4]
    else:
        fs.files[URI + "/" + file] = '{"session_id":'
    restarted = session(fs)
    await restarted.load()
    result = await restarted.add_messages_idempotent_async(specs(), "torn-root")
    assert result.replayed
    assert len(fs.files[URI + "/messages.jsonl"].splitlines()) == 2


@pytest.mark.parametrize("mutation", ["missing", "other-incarnation"])
async def test_receipt_unaware_restore_fails_closed(mutation):
    fs = setup()
    await session(fs).add_messages_idempotent_async(specs(), "restore")
    path = URI + "/.append_receipts/state.json"
    if mutation == "missing":
        del fs.files[path]
    else:
        record = json.loads(fs.files[path])
        record["incarnation"] = "0" * 32
        fs.files[path] = json.dumps(record)
    with pytest.raises(ConflictError, match="restore"):
        await session(fs).add_messages_idempotent_async(specs(), "restore")
    assert len(fs.files[URI + "/messages.jsonl"].splitlines()) == 2


async def test_capacity_never_evicts_old_keys_and_legacy_still_appends(monkeypatch):
    import openviking.session.append_receipts as receipts
    from openviking_cli.exceptions import ResourceExhaustedError

    fs = setup()
    monkeypatch.setattr(receipts, "MAX_RECEIPTS", 1)
    first = await session(fs).add_messages_idempotent_async(specs(), "first")
    with pytest.raises(ResourceExhaustedError):
        await session(fs).add_messages_idempotent_async(specs(), "second")
    assert (
        await session(fs).add_messages_idempotent_async(specs(), "first")
    ).result == first.result
    await session(fs).add_messages_async(specs("legacy"))
    assert len(fs.files[URI + "/messages.jsonl"].splitlines()) == 3


async def test_replay_does_not_repeat_privacy_or_expiry_reporting(monkeypatch):
    from unittest.mock import AsyncMock

    import openviking.session.session as owner

    fs = setup()
    current = session(fs)
    current._privacy_config_service = object()
    current._expiry_reporter = SimpleNamespace(
        report_after_append=AsyncMock(), record_report_failure=AsyncMock()
    )
    privacy = AsyncMock(side_effect=lambda *args: args[1])
    monkeypatch.setattr(owner, "extract_and_store_session_privacy", privacy)
    await current.add_messages_idempotent_async(specs(), "once")
    await current.add_messages_idempotent_async(specs(), "once")
    privacy.assert_awaited_once()
    current._expiry_reporter.report_after_append.assert_awaited_once()


async def test_ovpack_refuses_to_silently_drop_receipt_state(tmp_path):
    from openviking.storage.ovpack.operations import _validate_session_receipt_export
    from openviking_cli.exceptions import InvalidArgumentError

    fs = setup()
    entries = [{"uri": URI + "/.meta.json", "rel_path": ".meta.json", "isDir": False}]
    await _validate_session_receipt_export(fs, URI, entries, CTX)
    await session(fs).add_messages_idempotent_async(specs(), "backup")
    with pytest.raises(InvalidArgumentError, match="backend-level backup"):
        await _validate_session_receipt_export(fs, URI, entries, CTX)


def test_receipt_keys_are_scoped_to_account_and_authenticated_user():
    from openviking.session.append_receipts import AppendReceiptStore

    stores = [
        AppendReceiptStore(setup(), RequestContext(user=UserIdentifier(a, u), role=Role.USER), URI)
        for a, u in [("a", "u"), ("a", "v"), ("b", "u")]
    ]
    assert len({s.key_digest("same-key") for s in stores}) == 3


async def test_torn_intent_never_publishes_unverifiable_messages():
    fs = setup()
    original = fs.files[URI + "/messages.jsonl"]
    fs.fail_after = 2
    with pytest.raises(RuntimeError):
        await session(fs).add_messages_idempotent_async(specs(), "intent")
    fs.fail_after = None
    fs.files[URI + "/.append_receipts/pending.json"] = '{"version":'
    with pytest.raises(ConflictError):
        await session(fs).add_messages_idempotent_async(specs(), "intent")
    assert fs.files[URI + "/messages.jsonl"] == original
