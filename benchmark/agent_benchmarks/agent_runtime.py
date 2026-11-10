"""Run benchmark tools through the repository's VikingBot AgentLoop and VLM provider."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4

from benchmark.agent_benchmarks.memory import recall


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def callback_tool(name, description, parameters, callback):
    from vikingbot.agent.tools.base import Tool

    class BenchmarkTool(Tool):
        @property
        def name(self):
            return name

        @property
        def description(self):
            return description

        @property
        def parameters(self):
            return parameters

        async def execute(self, tool_context, **kwargs):
            result = await callback(**kwargs)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)

    return BenchmarkTool()


def build_agent(settings: dict, workspace: Path, output: Path, *, provider=None):
    """Isolate benchmark configuration; never load the user's bot config or skills."""
    from vikingbot.agent.loop import AgentLoop
    from vikingbot.bus.queue import MessageBus
    from vikingbot.config.schema import Config
    from vikingbot.providers.vlm_adapter import VLMProviderAdapter
    from vikingbot.sandbox.manager import SandboxManager

    from openviking.models.vlm import VLMFactory

    class BenchmarkSandboxManager(SandboxManager):
        def get_workspace_path(self, session_key):
            return self.workspace

    class CheckedProvider(VLMProviderAdapter):
        async def chat(self, *args, **kwargs):
            response = await super().chat(*args, **kwargs)
            if response.finish_reason == "error":
                raise RuntimeError("Benchmark model request failed; refusing to grade an API error")
            return response

        async def chat_stream(self, *args, **kwargs):
            async for event in super().chat_stream(*args, **kwargs):
                if event.response is not None and event.response.finish_reason == "error":
                    raise RuntimeError(
                        "Benchmark model request failed; refusing to grade an API error"
                    )
                yield event

    config = Config(
        storage_workspace=str(output / "bot-data"),
        agents={
            "model": settings["model"],
            "subagent_enabled": False,
            "session_context_enabled": False,
        },
        sandbox={"backend": "direct"},
        hooks=[],
        skills=[],
    )
    sandbox = BenchmarkSandboxManager(config, workspace, output / "empty-bootstrap")
    if provider is None:
        vlm = VLMFactory.create(settings)
        provider = CheckedProvider(vlm, default_model=settings["model"])
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=workspace,
        model=settings["model"],
        temperature=settings["temperature"],
        max_iterations=settings["max_iterations"],
        sandbox_manager=sandbox,
        config=config,
        eval=True,
    )


def workspace_tools():
    """Use the repository's file/shell tools against the benchmark workspace."""
    from vikingbot.agent.tools.filesystem import (
        EditFileTool,
        ListDirTool,
        ReadFileTool,
        WriteFileTool,
    )
    from vikingbot.agent.tools.shell import ExecTool

    return [ExecTool(timeout=120), ReadFileTool(), WriteFileTool(), EditFileTool(), ListDirTool()]


async def run_agent(
    settings: dict,
    prompt: str,
    tools: list,
    workspace: Path,
    output: Path,
    memory: dict,
    *,
    timeout: float,
    agent=None,
) -> dict:
    from vikingbot.agent.tools.registry import ToolRegistry
    from vikingbot.config.schema import SessionKey

    prompt, _ = await recall(prompt, memory, output)
    (output / "ov-prompt.md").write_text(prompt)
    agent = agent or build_agent(settings, workspace, output)
    registry = ToolRegistry(config=agent.config)
    for tool in tools:
        registry.register(tool)
    # Only the explicit benchmark tools are visible, regardless of the bot's defaults.
    messages = [
        {
            "role": "system",
            "content": "Complete the benchmark task using the supplied tools. Follow the task's authoritative instructions. Return your final answer when finished.",
        },
        {"role": "user", "content": prompt},
    ]
    session = SessionKey(type="benchmark", channel_id="train", chat_id=uuid4().hex)
    turns = []
    execution = {
        "runtime": "vikingbot.AgentLoop",
        "session_id": session.safe_name(),
        "status": "running",
        "text": "",
        "tool_executions": [],
    }
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(
            agent._run_agent_loop(
                messages=messages,
                session_key=session,
                publish_events=False,
                ov_tools_enable=False,
                openviking_tool_names=[],
                tool_registry=registry,
                captured_turns=turns,
                inject_write_experience=False,
                allow_final_fallback=False,
            ),
            timeout=timeout,
        )
        text, _, tools_used, usage, iterations = result
        execution.update(status="completed", text=text or "", iterations=iterations, usage=usage)
        execution["tool_executions"] = [
            {
                "tool_name": row["tool_name"],
                "arguments": row.get("resolved_args", json.loads(row["args"])),
                "content": row["result"],
                "backend": "vikingbot",
            }
            for row in tools_used
        ]
        return execution
    except BaseException as exc:
        execution.update(status="failed", error=type(exc).__name__)
        raise
    finally:
        execution.update(turns=turns, elapsed_sec=time.monotonic() - started)
        save(output / "execution.json", execution)
        await agent.close_mcp()
        if agent.sandbox_manager:
            await agent.sandbox_manager.cleanup_all()
        vlm = getattr(agent.provider, "_vlm", None)
        if vlm is not None and hasattr(vlm, "close"):
            vlm.close()


def trajectory_messages(prompt: str, execution: dict) -> list[dict]:
    """Preserve intermediate assistant text and structured tool calls/results for OV."""
    messages = [{"id": "task", "role": "user", "parts": [{"type": "text", "text": prompt}]}]
    for i, turn in enumerate(execution["turns"]):
        parts = []
        if turn.get("content"):
            parts.append({"type": "text", "text": turn["content"]})
        for j, tool in enumerate(turn.get("tool_calls", [])):
            parts.append(
                {
                    "type": "tool",
                    "tool_id": tool.get("tool_call_id", f"{i}-{j}"),
                    "tool_name": tool["tool_name"],
                    "tool_input": tool.get("resolved_args", json.loads(tool["args"])),
                    "tool_output": str(tool["result"]),
                    "tool_status": "completed" if tool["execute_success"] else "error",
                    "duration_ms": tool.get("duration", 0),
                }
            )
        if parts:
            messages.append({"id": f"turn-{i}", "role": "assistant", "parts": parts})
    messages.append(
        {
            "id": "answer",
            "role": "assistant",
            "parts": [{"type": "text", "text": execution["text"]}],
        }
    )
    return messages
