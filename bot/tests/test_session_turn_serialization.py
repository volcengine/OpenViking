# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for per-session turn serialization (issue #4756).

`process_direct` turns (cron ``on_job`` / heartbeat) enter the same
``_process_message`` pipeline as bus turns but from their own asyncio
task. Without a per-session turn lock, two turns mutate one cached
Session concurrently: the pair that arrived first can be persisted
behind the pair that interrupted it, and a turn released after ``/new``
re-appends its pair to the cleared session (ghost history).

Both tests park a turn inside the (fake) LLM call — deterministic, no
sleeps at the LLM boundary.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vikingbot.agent.loop import AgentLoop
from vikingbot.bus.events import InboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config, SessionKey
from vikingbot.providers.base import LLMProvider, LLMResponse


class _ParkedProvider(LLMProvider):
    """Parks every chat() call until release() — one turn inside at a time."""

    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.n_inside = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.n_inside += 1
        self.entered.set()
        await self.release.wait()
        return LLMResponse(content=f"reply-{self.n_inside}")

    def get_default_model(self) -> str:
        return "fake-model"


class _FakeSubagentManager:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _make_loop(temp_dir: Path, provider: LLMProvider) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=temp_dir / "workspace",
        config=Config(storage_workspace=str(temp_dir)),
        max_iterations=2,
    )


@pytest.mark.asyncio
async def test_process_direct_waits_for_inflight_bus_turn(temp_dir: Path, monkeypatch):
    """A cron turn cannot enter the LLM while a user turn is in flight, and
    the user pair is persisted before the cron pair that arrived later."""
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    key = SessionKey(type="cli", channel_id="default", chat_id="turn-order")
    provider = _ParkedProvider()
    loop = _make_loop(temp_dir, provider)

    user_task = asyncio.create_task(
        loop._process_message(
            InboundMessage(
                session_key=key, sender_id="alice", content="what is on my calendar today?"
            )
        )
    )
    await asyncio.wait_for(provider.entered.wait(), timeout=5)  # user turn parked in chat()

    cron_task = asyncio.create_task(
        loop.process_direct(
            "[CRON TASK] scheduled reminder: standup in 10 minutes", session_key=key
        )
    )
    await asyncio.sleep(0.1)
    assert provider.n_inside == 1, "cron turn must wait for the in-flight user turn"

    provider.release.set()
    await asyncio.wait_for(user_task, timeout=10)
    await asyncio.wait_for(cron_task, timeout=10)
    await asyncio.sleep(0.1)  # let turn-end background tasks settle

    session = loop.sessions.get_or_create(key)
    user_contents = [m["content"] for m in session.messages if m["role"] == "user"]
    assert user_contents == [
        "what is on my calendar today?",
        "[CRON TASK] scheduled reminder: standup in 10 minutes",
    ], "the message received first must be persisted first"


@pytest.mark.asyncio
async def test_new_command_waits_for_inflight_cron_turn(temp_dir: Path, monkeypatch):
    """`/new` cannot clear history while a cron turn is in flight; once the
    cron pair has been persisted, the cleared session stays empty."""
    monkeypatch.setattr(AgentLoop, "_register_builtin_hooks", lambda self: None)
    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    monkeypatch.setattr("vikingbot.agent.loop.SubagentManager", _FakeSubagentManager)

    key = SessionKey(type="cli", channel_id="default", chat_id="new-ghost")
    provider = _ParkedProvider()
    loop = _make_loop(temp_dir, provider)

    cron_task = asyncio.create_task(
        loop.process_direct(
            "[CRON TASK] scheduled reminder: standup in 10 minutes", session_key=key
        )
    )
    await asyncio.wait_for(provider.entered.wait(), timeout=5)  # cron turn parked in chat()

    new_task = asyncio.create_task(
        loop._process_message(
            InboundMessage(session_key=key, sender_id="alice", content="/new")
        )
    )
    await asyncio.sleep(0.1)
    assert not new_task.done(), "/new must wait for the in-flight cron turn"

    provider.release.set()
    await asyncio.wait_for(cron_task, timeout=10)
    new_response = await asyncio.wait_for(new_task, timeout=10)
    assert "New session started" in new_response.content

    session = loop.sessions.get_or_create(key)
    ghost = any("CRON TASK" in (m.get("content") or "") for m in session.messages)
    assert not ghost, "history confirmed dropped must not be resurrected by the cron turn"
