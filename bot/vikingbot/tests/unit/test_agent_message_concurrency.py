import asyncio

import pytest
from pydantic import ValidationError

from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage, OutboundMessage
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
    loop._idle_wakeup = asyncio.Event()
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


@pytest.mark.asyncio
async def test_semaphore_caps_cross_session_concurrency():
    loop = _loop(max_concurrency=2)
    current = 0
    peak = 0
    release = asyncio.Event()
    started = asyncio.Event()

    async def process(msg: InboundMessage):
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        if current >= 2:
            started.set()
        await release.wait()
        current -= 1
        return None

    loop._process_message = process
    for name in ("a", "b", "c"):
        await loop.bus.publish_inbound(_message(name, name))

    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)
    assert peak == 2
    assert current == 2

    release.set()
    loop.stop()
    await asyncio.wait_for(run_task, timeout=2.5)
    assert peak == 2


@pytest.mark.asyncio
async def test_error_in_one_session_does_not_block_another():
    loop = _loop(max_concurrency=2)
    finished = asyncio.Event()

    async def process(msg: InboundMessage):
        if msg.content == "boom":
            raise RuntimeError("boom")
        finished.set()
        return None

    loop._process_message = process
    await loop.bus.publish_inbound(_message("a", "boom"))
    await loop.bus.publish_inbound(_message("b", "ok"))

    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(finished.wait(), timeout=1)
    error = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1)
    loop.stop()
    await asyncio.wait_for(run_task, timeout=1)

    assert "boom" in error.content
    assert error.session_key.chat_id == "a"


@pytest.mark.asyncio
async def test_process_direct_serializes_with_same_session_bus_message():
    loop = _loop(max_concurrency=2)
    release = asyncio.Event()
    started = asyncio.Event()
    events: list[str] = []

    async def process(msg: InboundMessage):
        events.append(f"start:{msg.content}")
        started.set()
        await release.wait()
        events.append(f"end:{msg.content}")
        return None

    loop._process_message = process
    await loop.bus.publish_inbound(_message("a", "bus"))
    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), timeout=1)

    direct = asyncio.create_task(loop.process_direct("direct", _message("a", "direct").session_key))
    await asyncio.sleep(0)
    assert events == ["start:bus"]

    release.set()
    await asyncio.wait_for(direct, timeout=1)
    loop.stop()
    await asyncio.wait_for(run_task, timeout=2.5)

    assert events.index("end:bus") < events.index("start:direct")


@pytest.mark.asyncio
async def test_process_direct_shares_global_semaphore_with_bus():
    loop = _loop(max_concurrency=1)
    release = asyncio.Event()
    started = asyncio.Event()
    events: list[str] = []

    async def process(msg: InboundMessage):
        events.append(f"start:{msg.content}")
        started.set()
        await release.wait()
        events.append(f"end:{msg.content}")
        return None

    loop._process_message = process
    await loop.bus.publish_inbound(_message("a", "bus"))
    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), timeout=1)

    direct = asyncio.create_task(loop.process_direct("direct", _message("b", "direct").session_key))
    await asyncio.sleep(0)
    assert events == ["start:bus"]

    release.set()
    await asyncio.wait_for(direct, timeout=1)
    loop.stop()
    await asyncio.wait_for(run_task, timeout=2.5)

    assert events.index("end:bus") < events.index("start:direct")


@pytest.mark.asyncio
async def test_cancel_run_still_drains_in_flight_turn():
    loop = _loop(max_concurrency=1)
    release = asyncio.Event()
    started = asyncio.Event()
    events: list[str] = []

    async def process(msg: InboundMessage):
        events.append("start")
        started.set()
        await release.wait()
        events.append("end")
        return OutboundMessage(session_key=msg.session_key, content="done")

    loop._process_message = process
    await loop.bus.publish_inbound(_message("a", "a1"))
    run_task = asyncio.create_task(loop.run())
    await asyncio.wait_for(started.wait(), timeout=1)

    run_task.cancel()
    await asyncio.sleep(0)
    assert not run_task.done()
    assert events == ["start"]

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=1)

    assert events == ["start", "end"]
    outbound = await asyncio.wait_for(loop.bus.consume_outbound(), timeout=1)
    assert outbound.content == "done"
