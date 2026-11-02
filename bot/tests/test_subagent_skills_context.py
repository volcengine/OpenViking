# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for subagent prompt skill loading."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools.spawn import WaitSubagentsTool
from vikingbot.bus.queue import MessageBus


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["openai", "litellm"])
async def test_subagent_replays_deepseek_reasoning_after_tool(tmp_path, monkeypatch, backend):
    import httpx
    from openai import AsyncOpenAI
    from vikingbot.agent.tools.base import Tool
    from vikingbot.config.schema import SessionKey
    from vikingbot.providers.vlm_adapter import VLMProviderAdapter

    from openviking.models.vlm.backends.litellm_vlm import LiteLLMVLMProvider
    from openviking.models.vlm.backends.openai_vlm import OpenAIVLM

    class EchoTool(Tool):
        name = "echo"
        description = "Return a test result."
        parameters = {"type": "object", "properties": {}}

        async def execute(self, tool_context, **kwargs):
            return "tool result"

    calls = []

    def completion(request):
        payload = json.loads(request.content)
        calls.append(payload)
        message = {"role": "assistant", "content": "done", "reasoning_content": "finish"}
        if len(calls) == 1:
            message.update(
                content=None,
                reasoning_content="call echo first",
                tool_calls=[
                    {
                        "id": "call-echo",
                        "type": "function",
                        "function": {"name": "echo", "arguments": "{}"},
                    }
                ],
            )
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            },
        )

    async def send(client, request, **kwargs):
        return completion(request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)

    monkeypatch.setattr(
        "vikingbot.agent.tools.register_subagent_tools",
        lambda registry, config: registry.register(EchoTool()),
    )
    vlm_cls = OpenAIVLM if backend == "openai" else LiteLLMVLMProvider
    vlm = vlm_cls(
        {
            "provider": backend,
            "model": "deepseek-flash",
            "api_key": "test",
            "api_base": "https://deepseek.example/v1",
        }
    )
    bus = MessageBus()
    http_client = httpx.AsyncClient()
    async with AsyncOpenAI(
        api_key="test",
        base_url="https://deepseek.example/v1",
        http_client=http_client,
    ) as client:
        if backend == "openai":
            monkeypatch.setattr(vlm, "get_async_client", lambda: client)
        manager = SubagentManager(
            provider=VLMProviderAdapter(vlm, default_model="deepseek-flash"),
            workspace=tmp_path,
            bus=bus,
            config=SimpleNamespace(ov_server=SimpleNamespace(is_available=lambda: False)),
        )
        await manager._run_subagent(
            "test",
            "run echo",
            "echo task",
            SessionKey(type="cli", channel_id="default", chat_id="deepseek"),
        )

    assert len(calls) == 2
    assistant = next(message for message in calls[1]["messages"] if message["role"] == "assistant")
    assert assistant["reasoning_content"] == "call echo first"
    assert assistant["tool_calls"][0]["id"] == "call-echo"
    assert calls[1]["messages"][-1]["content"] == "tool result"
    announcement = await asyncio.wait_for(bus.consume_inbound(), timeout=1)
    assert "done" in announcement.content


def _write_skill(workspace: Path, name: str, content: str) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_subagent_prompt_loads_local_skills(tmp_path):
    _write_skill(
        tmp_path,
        "always-skill",
        """---
description: Always active instructions
always: true
---
# Always Skill

Always-loaded instruction.
""",
    )
    _write_skill(
        tmp_path,
        "normal-skill",
        """---
description: Normal on-demand instructions
---
# Normal Skill

Read this only when needed.
""",
    )

    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=tmp_path,
        bus=MessageBus(),
        config=SimpleNamespace(),
    )

    prompt = manager._build_subagent_prompt("inspect local files")

    assert "# Active Skills" in prompt
    assert "### Skill: always-skill" in prompt
    assert "Always-loaded instruction." in prompt
    assert "# Skills" in prompt
    assert "<name>normal-skill</name>" in prompt
    assert "<description>Normal on-demand instructions</description>" in prompt
    assert "<location>skills/normal-skill/SKILL.md</location>" in prompt


@pytest.mark.asyncio
async def test_subagent_prompt_loads_skills_from_session_workspace(tmp_path):
    source_workspace = tmp_path / "source"
    session_workspace = tmp_path / "sandboxes" / "session"
    _write_skill(
        source_workspace,
        "global-skill",
        """---
description: Global instructions
always: true
---
# Global Skill

Global-loaded instruction.
""",
    )
    _write_skill(
        session_workspace,
        "session-skill",
        """---
description: Session instructions
always: true
---
# Session Skill

Session-loaded instruction.
""",
    )

    class FakeSandboxManager:
        def __init__(self):
            self.created_for = []

        async def get_sandbox(self, session_key):
            self.created_for.append(session_key)
            return SimpleNamespace()

        def get_workspace_path(self, session_key):
            return session_workspace

    sandbox_manager = FakeSandboxManager()
    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=source_workspace,
        bus=MessageBus(),
        config=SimpleNamespace(),
        sandbox_manager=sandbox_manager,
    )
    session_key = SimpleNamespace()

    prompt_workspace = await manager._get_session_workspace(session_key)
    prompt = manager._build_subagent_prompt("inspect local files", workspace=prompt_workspace)

    assert sandbox_manager.created_for == [session_key]
    assert f"Your workspace is at: {session_workspace}" in prompt
    assert "### Skill: session-skill" in prompt
    assert "Session-loaded instruction." in prompt
    assert "Global-loaded instruction." not in prompt


@pytest.mark.asyncio
async def test_subagent_spawn_rejects_tasks_above_concurrency_limit(tmp_path, monkeypatch):
    blocker = asyncio.Event()
    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake-model"),
        workspace=tmp_path,
        bus=MessageBus(),
        config=SimpleNamespace(agents=SimpleNamespace(subagent_max_concurrency=1)),
    )

    async def blocked(*args, **kwargs):
        await blocker.wait()

    monkeypatch.setattr(manager, "_run_subagent", blocked)
    session_key = SimpleNamespace()

    first = await manager.spawn("first", session_key)
    second = await manager.spawn("second", session_key)

    assert "started" in first
    assert second == "Error: Subagent concurrency limit reached (1)"
    assert manager.get_running_count() == 1

    tasks = list(manager._running_tasks.values())
    blocker.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.sleep(0)

    third = await manager.spawn("third", session_key)

    assert "started" in third
    await asyncio.gather(*manager._running_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_managed_subagent_queue_collects_failure_and_cancels_pending_work(tmp_path):
    """Collection waits for outcomes, is cancellable, and preserves queued work until cleanup."""
    started, release = asyncio.Queue(), asyncio.Event()
    manager = SubagentManager(
        provider=SimpleNamespace(get_default_model=lambda: "fake"),
        workspace=tmp_path,
        bus=MessageBus(),
        config=SimpleNamespace(agents=SimpleNamespace(subagent_max_concurrency=1)),
    )

    async def run(task_id, task, session_key):
        await started.put(task)
        if task == "first":
            await release.wait()
            raise ValueError("source unreadable")
        await asyncio.Event().wait()

    manager.task_runner = run
    collecting = None
    try:
        await manager.spawn("first", SimpleNamespace())
        await manager.spawn("second", SimpleNamespace())
        assert await asyncio.wait_for(started.get(), 2) == "first"
        assert started.empty()
        assert "queue is full" in await manager.spawn("third", SimpleNamespace())
        assert manager.begin_submission().startswith("Error:")
        report = json.loads(
            await asyncio.wait_for(
                WaitSubagentsTool(manager).execute(SimpleNamespace(), block=False), 2
            )
        )
        assert report["results"] == []
        assert len(report["running"]) == len(report["queued"]) == 1
        assert report["max_concurrency"] == 1 and report["free_worker_slots"] == 0
        assert report["queue_capacity"] == report["capacity"] == 0
        collecting = asyncio.create_task(WaitSubagentsTool(manager).execute(SimpleNamespace()))
        await asyncio.sleep(0)
        assert not collecting.done()
        collecting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await collecting
        assert manager.get_running_count() == 1
        collecting = asyncio.create_task(WaitSubagentsTool(manager).execute(SimpleNamespace()))
        release.set()
        assert await asyncio.wait_for(started.get(), 2) == "second"
        report = json.loads(await asyncio.wait_for(collecting, 2))
        assert len(report["results"]) == 1 and report["capacity"] == 1
        assert report["queue_capacity"] == 1 and report["free_worker_slots"] == 0
        assert report["results"][0]["status"] == "failed"
        assert report["results"][0]["error"] == "source unreadable"
        assert not (await manager.wait(block=False))["results"]
        assert "accepted" in await manager.spawn("third", SimpleNamespace())
        await manager.cancel_all()
        report = await manager.wait()
        assert report["running"] == report["queued"] == []
        assert report["free_worker_slots"] == 1
        assert report["queue_capacity"] == report["capacity"] == 2
        assert started.empty()
        assert manager.get_running_count() == 0
        assert "closed" in await manager.spawn("late", SimpleNamespace())
        assert manager.bus.inbound.empty()
    finally:
        await manager.cancel_all()
        if collecting is not None:
            collecting.cancel()
            await asyncio.gather(collecting, return_exceptions=True)
