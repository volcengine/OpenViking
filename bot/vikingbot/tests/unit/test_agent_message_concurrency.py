import asyncio

import pytest
from pydantic import ValidationError

from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import AgentsConfig, SessionKey


def _message(chat_id: str, content: str) -> InboundMessage:
    return InboundMessage(
        sender_id="user",
        content=content,
        session_key=SessionKey(type="test", channel_id="channel", chat_id=chat_id),
    )


def _loop(max_concurrency: int) -> AgentLoop:
    loop = AgentLoop.__new__(AgentLoop)
    loop.bus = MessageBus()
    loop._running = False
    loop._message_semaphore = asyncio.Semaphore(max_concurrency)
    loop._message_tasks = set()
    loop._session_locks = {}
    loop._session_lock_users = {}

    async def connect_mcp() -> None:
        return None

    loop._connect_mcp = connect_mcp
    return loop


def test_message_concurrency_config_defaults_to_four_and_rejects_zero():
    assert AgentsConfig().message_max_concurrency == 4
    with pytest.raises(ValidationError):
        AgentsConfig(message_max_concurrency=0)


@pytest.mark.asyncio
async def test_agent_loop_runs_sessions_concurrently_but_serializes_each_session():
    loop = _loop(max_concurrency=2)
    release = asyncio.Event()
    started = asyncio.Event()
    events: list[str] = []

    async def process(msg: InboundMessage):
        events.append(f"start:{msg.content}")
        if len([event for event in events if event.startswith("start:")]) == 2:
            started.set()
        await release.wait()
        events.append(f"end:{msg.content}")
        return None

    loop._process_message = process
    await loop.bus.publish_inbound(_message("a", "a1"))
    await loop.bus.publish_inbound(_message("a", "a2"))
    await loop.bus.publish_inbound(_message("b", "b1"))

    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    while len(loop._message_tasks) < 3:
        await asyncio.sleep(0)

    assert set(events) == {"start:a1", "start:b1"}

    run_task.cancel()
    await asyncio.sleep(0)
    assert not run_task.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=1)

    assert events.index("end:a1") < events.index("start:a2")
    assert {event for event in events if event.startswith("end:")} == {
        "end:a1",
        "end:a2",
        "end:b1",
    }
