# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for the Feishu channel WebSocket lifecycle supervision.

The lark-oapi ws client can end up with a permanently dead connection while its
blocking start() call keeps running (its internal reconnect task dies silently).
These tests verify that the channel-level watchdog detects the dead connection
and rebuilds the client, that the ws thread survives start() crashes, and that
handler failures do not break subsequent message processing.

The tests use fake ws clients so they run without the lark-oapi SDK installed.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from vikingbot.bus.events import InboundMessage
from vikingbot.channels import feishu as feishu_module
from vikingbot.channels.feishu import FeishuChannel
from vikingbot.config.schema import FeishuChannelConfig


class FakeBus:
    """Minimal message bus capturing published inbound messages."""

    def __init__(self):
        self.inbound: list[InboundMessage] = []

    async def publish_inbound(self, msg: InboundMessage) -> None:
        self.inbound.append(msg)


class FakeConn:
    """Stands in for a websockets connection with an OPEN/CLOSED state."""

    def __init__(self, open_: bool = True):
        self.state = SimpleNamespace(name="OPEN" if open_ else "CLOSED")
        self.close_called = False

    async def close(self):
        self.close_called = True
        self.state = SimpleNamespace(name="CLOSED")


class FakeWsClient:
    """
    Mimics lark's ws Client.start(): blocks on the thread's event loop until the
    loop is stopped from outside (like run_until_complete of a sleep-forever
    task), or raises if configured to.
    """

    def __init__(self, conn=None, start_error: Exception | None = None):
        self._conn = conn
        self._auto_reconnect = True
        self._start_error = start_error
        self.started = threading.Event()

    def start(self) -> None:
        self.started.set()
        if self._start_error is not None:
            raise self._start_error
        loop = asyncio.get_event_loop()
        loop.run_forever()


def make_channel(monkeypatch, ws_client_factory) -> FeishuChannel:
    """Build a FeishuChannel wired to fake clients with fast supervision timings."""
    monkeypatch.setattr(feishu_module, "FEISHU_AVAILABLE", True)
    config = FeishuChannelConfig(app_id="cli_test", app_secret="secret", bot_name="TestBot")
    channel = FeishuChannel(config, FakeBus())
    channel._WS_HEALTH_CHECK_INTERVAL_SEC = 0.05
    channel._WS_UNHEALTHY_RESTART_SEC = 0.15
    channel._WS_RESTART_DELAY_SEC = 0.05
    channel._WS_THREAD_JOIN_TIMEOUT_SEC = 2
    monkeypatch.setattr(channel, "_build_rest_client", lambda: object())
    monkeypatch.setattr(channel, "_build_ws_client", ws_client_factory)
    return channel


async def wait_for(predicate, timeout: float = 5.0):
    """Poll predicate until true or fail the test on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    pytest.fail("condition not met within timeout")


async def run_channel(channel: FeishuChannel):
    task = asyncio.create_task(channel.start())
    # Give start() a chance to fail fast if wiring is broken
    await asyncio.sleep(0)
    assert not task.done(), "channel.start() exited immediately"
    return task


class TestConnAliveDetection:
    def test_no_client(self):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b"), FakeBus()
        )
        assert channel._ws_conn_alive() is False

    def test_client_without_conn(self):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b"), FakeBus()
        )
        channel._ws_client = FakeWsClient(conn=None)
        assert channel._ws_conn_alive() is False

    def test_open_and_closed_state(self):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b"), FakeBus()
        )
        channel._ws_client = FakeWsClient(conn=FakeConn(open_=True))
        assert channel._ws_conn_alive() is True
        channel._ws_client = FakeWsClient(conn=FakeConn(open_=False))
        assert channel._ws_conn_alive() is False

    def test_legacy_closed_property(self):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b"), FakeBus()
        )
        channel._ws_client = FakeWsClient(conn=SimpleNamespace(closed=False))
        assert channel._ws_conn_alive() is True
        channel._ws_client = FakeWsClient(conn=SimpleNamespace(closed=True))
        assert channel._ws_conn_alive() is False


class TestConnectionFailureRecovery:
    async def test_watchdog_rebuilds_client_when_connection_stays_dead(self, monkeypatch):
        """A client whose connection is dead (SDK reconnect silently gave up)
        must be replaced by a fresh client after the grace period."""
        clients: list[FakeWsClient] = []

        def factory():
            # First generation: permanently dead connection. Later: healthy.
            client = FakeWsClient(conn=None if not clients else FakeConn())
            clients.append(client)
            return client

        channel = make_channel(monkeypatch, factory)
        task = await run_channel(channel)
        try:
            await wait_for(lambda: len(clients) >= 2)
            # The dead generation must be neutralized so it cannot reconnect
            assert clients[0]._auto_reconnect is False
            # The replacement generation is considered healthy and is kept
            await asyncio.sleep(0.3)
            assert channel._ws_conn_alive() is True
        finally:
            await channel.stop()
            await asyncio.wait_for(task, timeout=5)

    async def test_watchdog_keeps_healthy_client(self, monkeypatch):
        """A healthy connection must never be restarted."""
        clients: list[FakeWsClient] = []

        def factory():
            client = FakeWsClient(conn=FakeConn())
            clients.append(client)
            return client

        channel = make_channel(monkeypatch, factory)
        task = await run_channel(channel)
        try:
            await wait_for(lambda: len(clients) == 1)
            # Wait well past the unhealthy-restart threshold
            await asyncio.sleep(0.5)
            assert len(clients) == 1
            assert clients[0]._auto_reconnect is True
        finally:
            await channel.stop()
            await asyncio.wait_for(task, timeout=5)

    async def test_ws_thread_retries_when_start_raises(self, monkeypatch):
        """If the SDK start() call raises (e.g. connect failure), the ws thread
        must keep rebuilding clients until one succeeds."""
        clients: list[FakeWsClient] = []

        def factory():
            error = RuntimeError("connect failed") if len(clients) < 2 else None
            client = FakeWsClient(
                conn=FakeConn() if error is None else None, start_error=error
            )
            clients.append(client)
            return client

        channel = make_channel(monkeypatch, factory)
        task = await run_channel(channel)
        try:
            await wait_for(lambda: len(clients) >= 3 and clients[2].started.is_set())
            await wait_for(channel._ws_conn_alive)
        finally:
            await channel.stop()
            await asyncio.wait_for(task, timeout=5)

    async def test_watchdog_respawns_dead_thread(self, monkeypatch):
        """If the ws thread itself dies, the watchdog must respawn it."""
        clients: list[FakeWsClient] = []

        def factory():
            client = FakeWsClient(conn=FakeConn())
            clients.append(client)
            return client

        channel = make_channel(monkeypatch, factory)
        task = await run_channel(channel)
        try:
            await wait_for(lambda: len(clients) == 1)
            # Kill the current generation's thread by superseding it directly
            with channel._ws_state_lock:
                channel._ws_generation += 1
                client, ws_loop = channel._ws_client, channel._ws_loop
            channel._neutralize_ws_client(client, ws_loop)
            await wait_for(lambda: not channel._ws_thread.is_alive())
            # Watchdog notices the dead thread and spawns a new generation
            await wait_for(lambda: len(clients) >= 2)
            await wait_for(channel._ws_conn_alive)
        finally:
            await channel.stop()
            await asyncio.wait_for(task, timeout=5)


class TestGatewayLifecycle:
    async def test_stop_terminates_thread_and_watchdog(self, monkeypatch):
        clients: list[FakeWsClient] = []

        def factory():
            client = FakeWsClient(conn=FakeConn())
            clients.append(client)
            return client

        channel = make_channel(monkeypatch, factory)
        task = await run_channel(channel)
        await wait_for(lambda: len(clients) == 1 and clients[0].started.is_set())
        thread = channel._ws_thread

        await channel.stop()
        await asyncio.wait_for(task, timeout=5)

        assert not thread.is_alive()
        assert channel._ws_client is None
        assert clients[0]._auto_reconnect is False


def _make_feishu_event(message_id: str, text: str):
    """Build the minimal object shape _on_message reads from a Feishu event."""
    message = SimpleNamespace(
        message_id=message_id,
        chat_id="oc_chat",
        chat_type="p2p",
        message_type="text",
        content=f'{{"text": "{text}"}}',
        mentions=None,
        root_id=None,
    )
    sender = SimpleNamespace(
        sender_type="user",
        sender_id=SimpleNamespace(open_id="ou_sender"),
    )
    return SimpleNamespace(event=SimpleNamespace(message=message, sender=sender))


class TestHandlerFailureRecovery:
    async def test_handler_exception_does_not_break_later_messages(self, monkeypatch):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b", bot_name="TestBot"),
            FakeBus(),
        )

        async def no_reaction(*args, **kwargs):
            return None

        monkeypatch.setattr(channel, "_add_reaction", no_reaction)
        monkeypatch.setattr(
            feishu_module, "load_config", lambda: SimpleNamespace(mode="debug")
        )

        # First event is malformed and raises inside the handler
        broken = SimpleNamespace(event=None)
        await channel._on_message(broken)

        # A well-formed event must still be processed and reach the bus
        await channel._on_message(_make_feishu_event("om_1", "hello"))
        assert len(channel.bus.inbound) == 1
        assert channel.bus.inbound[0].content == "hello"

    async def test_duplicate_messages_are_ignored(self, monkeypatch):
        channel = FeishuChannel(
            FeishuChannelConfig(app_id="a", app_secret="b", bot_name="TestBot"),
            FakeBus(),
        )

        async def no_reaction(*args, **kwargs):
            return None

        monkeypatch.setattr(channel, "_add_reaction", no_reaction)
        monkeypatch.setattr(
            feishu_module, "load_config", lambda: SimpleNamespace(mode="debug")
        )

        await channel._on_message(_make_feishu_event("om_dup", "hello"))
        await channel._on_message(_make_feishu_event("om_dup", "hello"))
        assert len(channel.bus.inbound) == 1
