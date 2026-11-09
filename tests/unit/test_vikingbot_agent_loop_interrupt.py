import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import BotMode, SessionKey


@pytest.mark.asyncio
async def test_dequeued_message_is_active_before_interrupt_runs(monkeypatch):
    session_key = SessionKey(type="openapi", channel_id="test", chat_id="session")
    message = InboundMessage(sender_id="user", content="hello", session_key=session_key)
    original_wait_for = asyncio.wait_for

    async def task_wrapping_wait_for(awaitable, timeout=None):
        return await asyncio.create_task(awaitable)

    monkeypatch.setattr(asyncio, "wait_for", task_wrapping_wait_for)

    class InterruptingBus(MessageBus):
        interrupt_task: asyncio.Task[bool]

        async def consume_inbound(self) -> InboundMessage:
            msg = await super().consume_inbound()
            self.interrupt_task = asyncio.create_task(agent.interrupt(msg.session_key))
            return msg

    bus = InterruptingBus()
    agent = object.__new__(AgentLoop)
    agent.bus = bus
    agent.config = SimpleNamespace(mode=BotMode.NORMAL)
    agent._running = False
    agent._active_turn = None
    agent._queued_interrupt_counts = {}
    agent._connect_mcp = AsyncMock()

    async def process_message(*_args, **_kwargs):
        agent._running = False
        return OutboundMessage(session_key=session_key, content="completed")

    async def complete_interrupted(msg: InboundMessage):
        agent._running = False
        return OutboundMessage(session_key=msg.session_key, content="")

    agent._process_message = AsyncMock(side_effect=process_message)
    agent._complete_interrupted_message = AsyncMock(side_effect=complete_interrupted)

    await bus.publish_inbound(message)
    await original_wait_for(agent.run(), timeout=1)

    assert await bus.interrupt_task is True
    agent._complete_interrupted_message.assert_awaited_once_with(message)
