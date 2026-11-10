# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Progress contract for the manual VikingBot compaction command."""

import asyncio
from types import SimpleNamespace

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage, OutboundEventType
from vikingbot.config.schema import SessionKey


class _Session:
    def __init__(self, key):
        self.key = key
        self.messages = [{"role": "user", "content": "remember this"}]
        self.metadata = {}

    def clone(self):
        return _Session(self.key)

    def clear(self):
        self.messages.clear()


class _Sessions:
    def __init__(self, session):
        self.session = session

    def get_or_create(self, _key, *, skip_heartbeat):
        assert skip_heartbeat
        return self.session

    async def save(self, _session):
        return None


class _Bus:
    def __init__(self):
        self.events = []

    async def publish_outbound(self, message):
        self.events.append(message)


@pytest.mark.asyncio
@pytest.mark.parametrize("openviking_session", [True, False], ids=["openviking", "legacy"])
async def test_manual_compact_publishes_progress_before_blocking(openviking_session):
    session_key = SessionKey(type="cli", channel_id="default", chat_id="test-session")
    bus = _Bus()
    operation_started = asyncio.Event()
    operation_release = asyncio.Event()

    async def block_commit(*_args, **_kwargs):
        operation_started.set()
        await operation_release.wait()
        return True

    async def block_legacy_consolidation(*_args, **_kwargs):
        operation_started.set()
        await operation_release.wait()

    loop = AgentLoop.__new__(AgentLoop)
    loop.bus = bus
    loop.sessions = _Sessions(_Session(session_key))
    loop.config = SimpleNamespace()
    loop._get_ov_tools_enable = lambda _key: False
    loop._metadata_memory_peer_ids = lambda _metadata: []
    loop._metadata_memory_owner_user_ids = lambda _metadata: []
    loop._get_channel_config = lambda _key: None
    loop._check_cmd_auth = lambda _message: True
    loop._ov_session_context_enabled = lambda: openviking_session
    loop._commit_openviking_session = block_commit
    loop._safe_consolidate_memory = block_legacy_consolidation

    task = asyncio.create_task(
        loop._process_message(
            InboundMessage(
                sender_id="user",
                content="/compact",
                session_key=session_key,
                metadata={},
            )
        )
    )
    await operation_started.wait()

    assert len(bus.events) == 1
    assert bus.events[0].event_type == OutboundEventType.PROGRESS
    assert bus.events[0].content == "Compacting session memory..."
    assert bus.events[0].metadata == {}
    assert not task.done()

    operation_release.set()
    response = await task
    assert response.content == "🐈 New session started. Memory consolidated."
