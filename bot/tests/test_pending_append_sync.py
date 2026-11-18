"""Lost-response recovery with the real Bot Session persistence owner."""

import asyncio
import copy
from types import SimpleNamespace

import pytest
from vikingbot.config.schema import SessionKey
from vikingbot.openviking_mount.append_sync import sync_pending_append
from vikingbot.openviking_mount.session_state import get_openviking_state, reset_openviking_state
from vikingbot.session.manager import Session, SessionManager


class Server:
    def __init__(self):
        self.receipts = {}
        self.calls = []
        self.messages = []
        self.identity = "synthetic-actor"
        self.lose_reply = False
        self.on_append = None

    async def session_append_identity(self, user_id):
        return self.identity

    async def append_messages(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        key = kwargs["idempotency_key"]
        if key not in self.receipts:
            self.messages.extend(kwargs["messages"])
            self.receipts[key] = {"added": len(kwargs["messages"])}
        if self.on_append:
            self.on_append()
        if self.lose_reply:
            raise TimeoutError("synthetic lost reply")
        return self.receipts[key]


def make_session():
    session = Session(key=SessionKey(type="test", channel_id="channel", chat_id="sync"))
    session.add_message("user", "synthetic question")
    return session


@pytest.mark.asyncio
async def test_disk_restart_replays_same_key_and_body(tmp_path):
    session = make_session()
    server = Server()
    manager = SessionManager(tmp_path)
    server.lose_reply = True
    with pytest.raises(TimeoutError):
        await sync_pending_append(session, server, "s", None, manager.save)
    restarted = SessionManager(tmp_path)._load(session.key)
    assert restarted is not None
    server.lose_reply = False
    await sync_pending_append(restarted, server, "s", None, manager.save)
    assert len(server.messages) == 1
    assert server.calls[0] == server.calls[1]
    loaded = SessionManager(tmp_path)._load(session.key)
    assert get_openviking_state(loaded)["last_synced_local_index"] == 0
    assert "pending_append" not in get_openviking_state(loaded)


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["before_send", "after_ack"])
async def test_local_write_failure_never_loses_pending_key(tmp_path, which):
    session, server = make_session(), Server()
    manager = SessionManager(tmp_path)
    writes = 0

    async def save(value):
        nonlocal writes
        writes += 1
        if writes == (1 if which == "before_send" else 2):
            raise OSError("synthetic disk full")
        await manager.save(value)

    with pytest.raises(OSError):
        await sync_pending_append(session, server, "s", None, save)
    assert len(server.calls) == (0 if which == "before_send" else 1)
    key = get_openviking_state(session)["pending_append"]["key"]
    await sync_pending_append(session, server, "s", None, manager.save)
    assert server.calls[-1]["idempotency_key"] == key
    assert len(server.messages) == 1


@pytest.mark.asyncio
async def test_new_local_messages_are_not_acknowledged_with_old_batch(tmp_path):
    session, server = make_session(), Server()
    server.on_append = lambda: session.add_message("assistant", "arrived during RPC")
    await sync_pending_append(session, server, "s", None, SessionManager(tmp_path).save)
    assert get_openviking_state(session)["last_synced_local_index"] == 0
    assert len(session.messages) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["actor", "history", "session"])
async def test_pending_identity_and_history_are_not_silently_rebound(tmp_path, change):
    session, server = make_session(), Server()
    manager = SessionManager(tmp_path)
    server.lose_reply = True
    with pytest.raises(TimeoutError):
        await sync_pending_append(session, server, "s", None, manager.save)
    server.lose_reply = False
    if change == "actor":
        server.identity = "different-actor"
    if change == "history":
        session.messages[0]["content"] = "changed"
    with pytest.raises(RuntimeError, match="reconcile"):
        await sync_pending_append(
            session, server, "other" if change == "session" else "s", None, manager.save
        )
    assert len(server.calls) == 1
    with pytest.raises(RuntimeError, match="pending"):
        reset_openviking_state(session, rotate_session_id=True)


@pytest.mark.asyncio
async def test_cancellation_after_server_acceptance_keeps_persisted_batch(tmp_path):
    session, server = make_session(), Server()
    manager = SessionManager(tmp_path)
    accepted = asyncio.Event()
    append = server.append_messages

    async def wait_after_acceptance(**kwargs):
        await append(**kwargs)
        accepted.set()
        await asyncio.Event().wait()

    server.append_messages = wait_after_acceptance
    task = asyncio.create_task(sync_pending_append(session, server, "s", None, manager.save))
    await asyncio.wait_for(accepted.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    server.append_messages = append
    restarted = SessionManager(tmp_path)._load(session.key)
    await sync_pending_append(restarted, server, "s", None, manager.save)
    assert len(server.messages) == 1


@pytest.mark.asyncio
async def test_hook_resolves_existing_pending_even_after_option_is_disabled(tmp_path):
    from vikingbot.hooks.base import HookContext
    from vikingbot.hooks.builtins.openviking_hooks import OpenVikingCompactHook

    session, server = make_session(), Server()
    manager = SessionManager(tmp_path)
    server.lose_reply = True
    with pytest.raises(TimeoutError):
        await sync_pending_append(session, server, "s", None, manager.save)
    state = get_openviking_state(session)
    state["session_id"] = "s"
    server.lose_reply = False
    server.session_owner_user_id = lambda: None

    async def get_session(*args, **kwargs):
        return {"pending_tokens": 0}

    server.get_session = get_session
    result = await OpenVikingCompactHook()._execute_session_context_commit(
        HookContext(event_type="message.compact", session_key=session.key),
        session,
        server,
        SimpleNamespace(session_append_idempotency=False),
        "admin",
        force_commit=False,
        keep_recent_turn_count=3,
        min_raw_tail_steps=1,
        commit_message_threshold=None,
        persist_session=manager.save,
    )
    assert result["success"] and len(server.messages) == 1
    assert "pending_append" not in state


@pytest.mark.asyncio
async def test_real_bot_adapter_uses_stable_chunk_keys_and_the_sdk_append_owner():
    from unittest.mock import AsyncMock

    from vikingbot.openviking_mount.ov_server import VikingClient

    adapter = VikingClient.__new__(VikingClient)
    sdk = SimpleNamespace(
        append_messages=AsyncMock(return_value={"added": 100, "message_count": 200}),
        batch_add_messages=AsyncMock(),
        _url="https://example.invalid",
        _api_key="synthetic",
        _user_id="actor",
    )
    adapter.ensure_session = AsyncMock()
    adapter._session_client_for_user = AsyncMock(return_value=sdk)
    messages = [{"role": "user", "content": str(i)} for i in range(101)]
    await adapter.append_messages("s", messages, idempotency_key="frozen")
    await adapter.append_messages("s", messages, idempotency_key="frozen")
    calls = sdk.append_messages.await_args_list
    assert len(calls) == 4
    assert calls[0] == calls[2] and calls[1] == calls[3]
    assert calls[0].kwargs["idempotency_key"] != calls[1].kwargs["idempotency_key"]
    assert [len(c.kwargs["messages"]) for c in calls] == [100, 1, 100, 1]
    sdk.batch_add_messages.assert_not_called()
    identity = await adapter.session_append_identity()
    assert "synthetic" not in identity
    sdk._user_id = "another"
    assert await adapter.session_append_identity() != identity


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, {}, [], ""])
async def test_invalid_pending_is_never_silently_replaced(tmp_path, invalid):
    session, server = make_session(), Server()
    get_openviking_state(session)["pending_append"] = invalid
    with pytest.raises(RuntimeError, match="reconcile"):
        await sync_pending_append(session, server, "s", None, SessionManager(tmp_path).save)
    assert not server.calls
    with pytest.raises(RuntimeError, match="pending"):
        reset_openviking_state(session)
