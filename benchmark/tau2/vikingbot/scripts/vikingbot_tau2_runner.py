#!/usr/bin/env python3
"""Run a single tau2-bench task with VikingBot AgentLoop + Tau2 tools.

Key points:
1) Tau2BenchEnv is initialized once and exposes tools via Tau2BenchToolProvider.call_tool().
2) VikingBot already has its own multi-iteration agent loop, so we call it once.
3) We register Tau2 tools into VikingBot ToolRegistry and let the agent decide.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import re

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tau2_env.tau2_tool_provider import Tau2BenchToolProvider, load_task_id

try:
    from vikingbot.agent.loop import AgentLoop
    from vikingbot.agent.tools.base import Tool, ToolContext
    from vikingbot.bus.queue import MessageBus
    from vikingbot.cli.commands import _init_bot_data, _make_provider
    from vikingbot.config.loader import ensure_config
    from vikingbot.config.schema import SessionKey
    from vikingbot.sandbox.manager import SandboxManager
    from vikingbot.session.manager import SessionManager
    from vikingbot.utils.helpers import get_source_workspace_path
except ImportError as exc:
    raise RuntimeError(
        "Failed to import vikingbot. Make sure OpenViking/vikingbot is installed "
        "or available on PYTHONPATH."
    ) from exc


class Tau2Tool(Tool):
    """Bridge tau2 tool schema into VikingBot Tool interface."""

    def __init__(self, schema: dict[str, Any], provider: Tau2BenchToolProvider):
        self._schema = schema
        self._provider = provider
        function_def = schema.get("function", {}) if isinstance(schema, dict) else {}
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

    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        return self._provider.call_tool(self._name, kwargs)


def _build_agent(config_path: str | None) -> AgentLoop:
    config = ensure_config(Path(config_path).expanduser() if config_path else None)
    _init_bot_data(config)

    bus = MessageBus()
    session_manager = SessionManager(config.bot_data_path)

    sandbox_parent_path = config.workspace_path
    source_workspace_path = get_source_workspace_path()
    sandbox_manager = SandboxManager(config, sandbox_parent_path, source_workspace_path)

    provider = _make_provider(config)

    return AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=config.agents.model,
        # max_iterations=config.agents.max_tool_iterations,
        max_iterations=30,
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


TAU2_SIM_TIME = "2024-05-15 15:00 (Wednesday) (EST)"
MEMORY_PROMPT_SUFFIX = (
    "---\n\nReply in the same language as the user's query, ignoring the language of "
    "the reference materials. User's query:"
)

SCOPE_PROMPT = """
<openviking_memory_scope_guard>
OpenViking memories are advisory. Use them only when their trigger, preconditions,
and applicability boundary match the current retail task.

- Do not broaden the user's requested replacement, return, exchange, cancellation,
  address-change, or payment scope because a retrieved memory describes a nearby
  workflow.
- If the user restricts the request to the current order, same order, observed
  order items, or a specific product variant, choose write arguments only from the
  current tool observations or an explicitly requested catalog lookup.
- Before a write tool call, order IDs, item IDs, new item IDs, payment method IDs,
  addresses, amounts, and refund/payment direction must be grounded in user input,
  recent tool observations, profile/order state, or an explicit catalog lookup.
- If a memory and the current task disagree, follow the current task state and the
  domain policy.
</openviking_memory_scope_guard>
"""

def _extract_memory_content(content: str) -> str | None:
    """Extract the memory block content from VikingBot's memory prompt wrapper."""
    if "## Current Session" not in content or MEMORY_PROMPT_SUFFIX not in content:
        return None

    prefix = (
        f"## Current Time: {TAU2_SIM_TIME}\n\n---\n\n## Current Session\n"
        "Channel: cli\n\n---\n\n"
    )
    if content.startswith(prefix) and content.endswith(MEMORY_PROMPT_SUFFIX):
        return content[len(prefix) : -len(MEMORY_PROMPT_SUFFIX)]

    match = re.search(
        r"## Current Session\nChannel: cli\n\n---\n\n(?P<memory>.*)"
        r"---\n\nReply in the same language as the user's query, ignoring the language of "
        r"the reference materials. User's query:$",
        content,
        re.DOTALL,
    )
    if match:
        return match.group("memory")
    return None


def _patch_sim_time(messages: list[dict[str, Any]]) -> None:
    """Replace VikingBot's real-world current time with tau2's simulated time."""
    for msg in messages:
        if msg.get("role") == "user" and isinstance(msg.get("content"), str):
            if "## Current Time:" in msg["content"]:
                msg["content"] = re.sub(
                    r"## Current Time: [^\n]+",
                    f"## Current Time: {TAU2_SIM_TIME}",
                    msg["content"],
                )
                msg["content"] = msg["content"] + SCOPE_PROMPT
                return


async def _run_agent(
    agent: AgentLoop,
    system_prompt: str,
    user_prompt: str,
    session_key: SessionKey,
    sender_id: str | None,
    agent_id: str | None,
    keep_default_tools: bool,
    messages_output_path: Path | None,
) -> tuple[str | None, str | None, dict[str, int], int, str | None]:
    messages = await agent.context.build_messages(
        history=[],
        current_message=user_prompt,
        session_key=session_key,
        ov_tools_enable=keep_default_tools,
        media=None,
        profile_user_list=[],
        memory_users=agent_id,
    )
    _patch_sim_time(messages)
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
    if messages_output_path is not None:
        messages_output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(messages_output_path, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
        print(f"Saved Full Messages: {messages_output_path}")
    return (*result, memory_content)


def _derive_messages_path(output_path: Path) -> Path:
    parent_parts = [part.replace("result", "trajectory") for part in output_path.parent.parts]
    base_path = Path(*parent_parts) / output_path.name

    if base_path.name.endswith("_trajectory.json"):
        return base_path.with_name(base_path.name.replace("_trajectory.json", "_messages.json"))
    return base_path.with_name(f"{base_path.stem}_messages{base_path.suffix}")


def main() -> None:
    parser = argparse.ArgumentParser(description="VikingBot tau2-bench runner")
    parser.add_argument("--data-split", required=True, help="e.g. telecom_test")
    parser.add_argument("--task-no", type=int, required=True, help="index in split list")
    parser.add_argument("--output", default="./result/tau2_trajectory.json")
    parser.add_argument(
        "--continue",
        dest="continue_run",
        action="store_true",
        help="If output file exists, skip running and exit",
    )
    parser.add_argument("--sender", default="tau2_user")
    parser.add_argument("--agent-id", default="", help="airline_v0 domain split workspace")
    parser.add_argument("--session", default=None)
    parser.add_argument("--config", default=None, help="ov.conf path (optional)")
    parser.add_argument(
        "--keep-default-tools",
        action="store_true",
        help="Keep VikingBot default tools (default: only tau2 tools)",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Override agent max iterations for this run",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    if output_path.exists():
        if args.continue_run:
            print(f"[Runner] Output exists, skip run: {output_path}")
            return
        print(f"[Runner] Output exists, will overwrite: {output_path}")

    domain, task_id = load_task_id(args.data_split, args.task_no)
    print("[Runner] Initializing tau2 environment...", flush=True)
    provider = Tau2BenchToolProvider(domain, task_id)
    provider.reset()

    user_query = provider.user_query
    policy = provider.policy
    ground_truth = provider.ground_truth

    agent = _build_agent(args.config)
    if args.max_iterations is not None:
        agent.max_iterations = args.max_iterations

    if not args.keep_default_tools:
        for tool_name in list(agent.tools.tool_names):
            # print(tool_name)
            agent.tools.unregister(tool_name)
    
    agent.tools.unregister("openviking_memory_commit")
    for schema in provider.list_openai_tools():
        agent.tools.register(Tau2Tool(schema, provider))

    instructions = []
    if policy:
        instructions.append(policy)
    instructions.append("Use the provided tools to interact with the environment.")
    if args.keep_default_tools:
        # instructions.append("Before you attend to customer, you MUST utilize openviking tools such as openviking_multi_read to read relevant agent memory that stores experiences distilled from similar tasks and carefully learn them.")
        instructions.append("Before you attend to customer, you MUST read relevant agent memory that stores experiences distilled from similar tasks and carefully learn them.")
    instructions.append(
        "If you need to communicate with the user, you MUST call tool `communicate_with_user`."
    )
    instructions.append("When the task is finished or terminated, call tool `done` first and output an ending content without using any tool calling for the next round to exit.")

    system_prompt = "\n".join(instructions)
    user_prompt = user_query

    session_id = args.session or f"tau2_{args.data_split}_{args.task_no}"
    session_key = SessionKey(type="cli", channel_id="tau2", chat_id=session_id)

    messages_output_path = _derive_messages_path(output_path)

    final_content, final_reasoning_content, tools_used, token_usage, iteration, memory_content = asyncio.run(
        _run_agent(
            agent,
            system_prompt,
            user_prompt,
            session_key,
            args.sender,
            args.agent_id,
            args.keep_default_tools,
            messages_output_path,
        )
    )

    reward = None
    evaluation_result = None
    if provider.env is not None:
        try:
            reward, evaluation_result = provider.env.env._get_reward()
        except Exception:
            pass

    trajectory = {
        "data_split": args.data_split,
        "task_no": args.task_no,
        "domain": domain,
        "task_id": task_id,
        "user_query": user_query,
        "policy": policy,
        "ground_truth": ground_truth,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "final_content": final_content,
        "reward": reward,
        "evaluation_result": evaluation_result,
        "tools_used": tools_used,
        "token_usage": token_usage,
        "iterations": iteration,
        "memory": memory_content,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(trajectory, f, ensure_ascii=False, indent=2)

    print(f"Saved: {output_path}")


if __name__ == "__main__":
    main()
