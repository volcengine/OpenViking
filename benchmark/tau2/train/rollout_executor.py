#!/usr/bin/env python3
"""Tau2 RolloutExecutor implementation for batch policy training."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openviking.message import Message, TextPart
from openviking.session.train import Case, ExecutionContext, ExperienceSet, Rollout


def _tool_provider_cls():
    from benchmark.tau2.common.tau2_env.tau2_tool_provider import Tau2BenchToolProvider

    return Tau2BenchToolProvider


def _vikingbot_imports() -> dict[str, Any]:
    try:
        from vikingbot.agent.loop import AgentLoop
        from vikingbot.agent.tools.base import Tool
        from vikingbot.bus.queue import MessageBus
        from vikingbot.cli.commands import _init_bot_data, _make_provider
        from vikingbot.config.loader import ensure_config
        from vikingbot.config.schema import SessionKey
        from vikingbot.sandbox.manager import SandboxManager
        from vikingbot.session.manager import SessionManager
        from vikingbot.utils.helpers import get_source_workspace_path
    except ImportError as exc:  # pragma: no cover - benchmark environment dependency
        raise RuntimeError(
            "Failed to import vikingbot. Source benchmark/tau2/vikingbot/setup_env.sh first."
        ) from exc

    return {
        "AgentLoop": AgentLoop,
        "Tool": Tool,
        "MessageBus": MessageBus,
        "_init_bot_data": _init_bot_data,
        "_make_provider": _make_provider,
        "ensure_config": ensure_config,
        "SessionKey": SessionKey,
        "SandboxManager": SandboxManager,
        "SessionManager": SessionManager,
        "get_source_workspace_path": get_source_workspace_path,
    }


def _make_tau2_tool(schema: dict[str, Any], provider: Any):
    Tool = _vikingbot_imports()["Tool"]

    class Tau2Tool(Tool):
        """Bridge tau2 tool schema into VikingBot Tool interface."""

        def __init__(self, tool_schema: dict[str, Any], tool_provider: Any):
            self._schema = tool_schema
            self._provider = tool_provider
            function_def = tool_schema.get("function", {}) if isinstance(tool_schema, dict) else {}
            self._name = function_def.get("name", "")
            self._description = function_def.get("description", "")
            self._parameters = function_def.get("parameters", {})

        @property
        def name(self) -> str:
            return self._name

        @property
        def description(self) -> str:
            return self._description

        @property
        def parameters(self) -> dict[str, Any]:
            return self._parameters

        async def execute(self, tool_context: Any, **kwargs: Any) -> str:
            del tool_context
            return self._provider.call_tool(self._name, kwargs)

    return Tau2Tool(schema, provider)


@dataclass(slots=True)
class Tau2RolloutExecutor:
    """Execute tau2 cases with VikingBot agent loop and tau2 tools."""

    config_path: str | None = None
    concurrency: int = 20
    keep_default_tools: bool = True
    max_iterations: int = 30

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        del policy_set
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        semaphore = asyncio.Semaphore(self.concurrency)

        async def run_one(case: Case) -> Rollout:
            async with semaphore:
                return await self._execute_one(case, context)

        return list(await asyncio.gather(*(run_one(case) for case in cases)))

    async def _execute_one(self, case: Case, context: ExecutionContext) -> Rollout:
        return await asyncio.to_thread(self._execute_one_sync, case, context)

    def _execute_one_sync(self, case: Case, context: ExecutionContext) -> Rollout:
        domain = str(case.input["domain"])
        task_id = str(case.input["task_id"])
        task_no = int(case.input["task_no"])
        data_split = str(case.input["data_split"])
        data_root = case.input.get("data_root")

        Tau2BenchToolProvider = _tool_provider_cls()
        provider = Tau2BenchToolProvider(domain, task_id, data_root=data_root)
        provider.reset()
        agent = _build_agent(self.config_path, max_iterations=self.max_iterations)
        _configure_tools(agent, provider, keep_default_tools=self.keep_default_tools)

        system_prompt = _build_system_prompt(
            provider.policy,
            keep_default_tools=self.keep_default_tools,
        )
        user_prompt = provider.user_query
        SessionKey = _vikingbot_imports()["SessionKey"]
        session_key = SessionKey(
            type="cli",
            channel_id="tau2",
            chat_id=f"tau2_{data_split}_{task_no}",
        )
        final_content, final_reasoning_content, tools_used, token_usage, iteration, memory_content = (
            _run_agent_sync(
                agent=agent,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                session_key=session_key,
                sender_id="tau2_user",
                keep_default_tools=self.keep_default_tools,
            )
        )
        reward = None
        evaluation_result = None
        if provider.env is not None:
            try:
                reward, evaluation_result = provider.env.env._get_reward()
            except Exception:
                reward = None
                evaluation_result = None

        return Rollout(
            case=case,
            messages=_build_rollout_messages(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tools_used=tools_used,
                final_content=final_content,
                evaluation_result=evaluation_result,
                reward=reward,
            ),
            policy_snapshot_id=context.policy_snapshot_id,
            metadata={
                "domain": domain,
                "data_split": data_split,
                "task_no": task_no,
                "task_id": task_id,
                "reward": reward,
                "evaluation_result": evaluation_result,
                "tools_used": tools_used,
                "token_usage": token_usage,
                "iterations": iteration,
                "memory": memory_content,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "final_content": final_content,
                "final_reasoning_content": final_reasoning_content,
                "keep_default_tools": self.keep_default_tools,
                "execution_metadata": dict(context.metadata),
            },
        )


def _build_agent(config_path: str | None, *, max_iterations: int):
    imports = _vikingbot_imports()
    config = imports["ensure_config"](Path(config_path).expanduser() if config_path else None)
    imports["_init_bot_data"](config)
    bus = imports["MessageBus"]()
    session_manager = imports["SessionManager"](config.bot_data_path)
    sandbox_parent_path = config.workspace_path
    source_workspace_path = imports["get_source_workspace_path"]()
    sandbox_manager = imports["SandboxManager"](config, sandbox_parent_path, source_workspace_path)
    provider = imports["_make_provider"](config)
    return imports["AgentLoop"](
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.model,
        max_iterations=max_iterations,
        memory_window=config.agents.memory_window,
        brave_api_key=config.tools.web.search.api_key or None,
        exa_api_key=None,
        gen_image_model=config.agents.gen_image_model,
        exec_config=config.tools.exec,
        cron_service=None,
        session_manager=session_manager,
        sandbox_manager=sandbox_manager,
        config=config,
        eval=True,
        mcp_servers=None,
    )


def _configure_tools(
    agent: Any,
    provider: Any,
    *,
    keep_default_tools: bool,
) -> None:
    if not keep_default_tools:
        for tool_name in list(agent.tools.tool_names):
            agent.tools.unregister(tool_name)
    agent.tools.unregister("openviking_memory_commit")
    for schema in provider.list_openai_tools():
        agent.tools.register(_make_tau2_tool(schema, provider))


def _build_system_prompt(policy: str, *, keep_default_tools: bool) -> str:
    instructions = []
    if policy:
        instructions.append(policy)
    instructions.append("Use the provided tools to interact with the environment.")
    if keep_default_tools:
        instructions.append(
            "Before you attend to customer, you MUST read relevant agent memory that stores "
            "experiences distilled from similar tasks and carefully learn them."
        )
    instructions.append(
        "If you need to communicate with the user, you MUST call tool `communicate_with_user`."
    )
    instructions.append(
        "When the task is finished or terminated, call tool `done` first and output an ending "
        "content without using any tool calling for the next round to exit."
    )
    return "\n".join(instructions)


def _run_agent_sync(
    *,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    session_key: Any,
    sender_id: str,
    keep_default_tools: bool,
):
    return asyncio.run(
        _run_agent(
            agent=agent,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            session_key=session_key,
            sender_id=sender_id,
            keep_default_tools=keep_default_tools,
        )
    )


async def _run_agent(
    *,
    agent: Any,
    system_prompt: str,
    user_prompt: str,
    session_key: Any,
    sender_id: str,
    keep_default_tools: bool,
):
    messages = await agent.context.build_messages(
        history=[],
        current_message=user_prompt,
        session_key=session_key,
        ov_tools_enable=keep_default_tools,
        media=None,
        profile_user_list=[],
    )
    if system_prompt:
        messages.insert(1, {"role": "system", "content": system_prompt})
    memory_content = None
    if len(messages) > 2 and isinstance(messages[2].get("content"), str):
        memory_content = _extract_memory_content(messages[2]["content"])
    result = await agent._run_agent_loop(
        messages=messages,
        session_key=session_key,
        publish_events=False,
        sender_id=sender_id,
        ov_tools_enable=keep_default_tools,
    )
    return (*result, memory_content)


MEMORY_PROMPT_PREFIX = "## Current Session\nChannel: cli\n\n---\n\n"
MEMORY_PROMPT_SUFFIX = (
    "---\n\nReply in the same language as the user's query, ignoring the language of "
    "the reference materials. User's query:"
)


def _extract_memory_content(content: str) -> str | None:
    start = content.find(MEMORY_PROMPT_PREFIX)
    end = content.rfind(MEMORY_PROMPT_SUFFIX)
    if start == -1 or end == -1:
        return None
    start += len(MEMORY_PROMPT_PREFIX)
    if start > end:
        return None
    return content[start:end]


def _build_rollout_messages(
    *,
    system_prompt: str,
    user_prompt: str,
    tools_used: Any,
    final_content: str | None,
    evaluation_result: Any,
    reward: Any,
) -> list[Message]:
    messages = [
        _message("tau2-system", "user", f"system:\n{system_prompt}"),
        _message("tau2-user", "user", user_prompt),
    ]
    if isinstance(tools_used, list):
        for idx, tool_info in enumerate(tools_used):
            if not isinstance(tool_info, dict):
                continue
            tool_name = tool_info.get("tool_name", "")
            args = tool_info.get("args", "")
            if tool_name:
                messages.append(
                    _message(
                        f"tau2-tool-call-{idx}",
                        "assistant",
                        f"tool-call:\nname: {tool_name}\narguments: {args}",
                    )
                )
            if tool_info.get("result") is not None:
                messages.append(
                    _message(f"tau2-tool-result-{idx}", "user", f"tool-response:\n{tool_info['result']}")
                )
    messages.append(_message("tau2-final", "assistant", final_content or ""))
    success = reward == 1 or reward == 1.0
    messages.append(
        _message(
            "tau2-reward",
            "user",
            f"task_success: {success}\ntask_reward: {reward}\nevaluation report: {evaluation_result}",
        )
    )
    return messages


def _message(message_id: str, role: str, text: str) -> Message:
    return Message(id=message_id, role=role, parts=[TextPart(text=text)])
