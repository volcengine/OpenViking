"""Tool setup and ordered execution of one model tool turn."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from openviking.utils.media_limits import MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
from vikingbot.agent.loop.loop_compaction import (
    _demote_historical_tool_media,
    _inline_media_bytes,
)
from vikingbot.agent.remote_skills import SkillRuntimeContext
from vikingbot.agent.skills import SkillsLoader
from vikingbot.agent.tools import register_default_tools
from vikingbot.agent.tools.base import MultimodalToolResult
from vikingbot.agent.tools.registry import ToolExecutionResult, ToolRegistry
from vikingbot.bus.events import OutboundEventType, OutboundMessage
from vikingbot.config.schema import SessionKey
from vikingbot.sandbox import SandboxManager
from vikingbot.utils.helpers import cal_str_tokens


def _is_tool_result_success(result: Any) -> bool:
    if result is None or isinstance(result, Exception):
        return False
    text = str(result).lstrip()
    return bool(text) and not text.startswith("Error:")


@dataclass(frozen=True)
class _LoopRequestContext:
    """Request values; the registry still creates an isolated context for each tool."""

    session_key: SessionKey
    sender_id: str | None
    actor_peer_id: str | None
    memory_peer_ids: list[str] | None
    memory_owner_user_ids: list[str] | None
    openviking_connection: dict[str, Any] | None
    channel_metadata: dict[str, Any] | None
    skill_runtime: Any | None


@dataclass(slots=True)
class _PlainTextContext:
    """Context passed to an `on_plain_text` callback when the model emits plain text."""

    messages: list[dict]
    session_key: SessionKey
    text: str
    reasoning_content: str | None
    iteration: int
    tools: ToolRegistry
    sandbox_manager: SandboxManager | None
    sender_id: str | None
    memory_peer_ids: list[str] | None
    memory_owner_user_ids: list[str] | None
    openviking_connection: dict[str, Any] | None


@dataclass(slots=True)
class _PlainTextDelivered:
    """Signal that the text was delivered externally; continue the loop with new state."""

    messages: list[dict]
    tools_used: list[dict]
    user_terminates: bool = False


@dataclass(slots=True)
class _PlainTextFinal:
    """Signal that the text should be treated as the final reply; exit the loop."""

    content: str | None = None


class AgentIterationLimitExceeded(RuntimeError):
    """A structured task used every available AgentLoop iteration without submitting."""

    def __init__(self, max_iterations: int, *, usage: dict[str, Any] | None = None):
        self.max_iterations = max_iterations
        self.usage = usage or {}
        super().__init__(
            f"Agent reached its {max_iterations}-iteration limit without submitting a valid bundle"
        )


def render_budget_reminder(
    remaining: int,
    thresholds: tuple[int, int, int] = (15, 8, 3),
) -> str | None:
    """Return a submission reminder within descending heads-up, warning and critical thresholds."""
    heads_up, warn, critical = thresholds
    remaining = max(0, remaining)
    if remaining <= critical:
        return (
            f"还剩 {remaining} 轮。收集子任务结果，检查并调用 submit_wiki_bundle；不再开启新任务。"
        )
    elif remaining <= warn:
        return (
            f"还剩 {remaining} 轮。必须开始提交：把当前最好结果通过 submit_wiki_bundle "
            "提交，不要追求完美。"
        )
    elif remaining <= heads_up:
        return (
            f"还剩 {remaining} 轮。请停止对已读文件的全量重扫，开始把已有发现收敛成最终产物，"
            "并准备调用 submit_wiki_bundle。"
        )
    return None


class LoopToolMixin:
    """Tool setup and ordered execution of one model tool turn. State is owned by AgentLoop."""

    async def _connect_mcp(self) -> None:
        """Connect to configured MCP servers (one-time, lazy, retryable on failure).

        Ported from HKUDS/nanobot v0.1.5.
        """
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        try:
            from vikingbot.agent.tools.mcp import connect_mcp_servers

            self._mcp_stack = AsyncExitStack()
            await self._mcp_stack.__aenter__()
            await connect_mcp_servers(self._mcp_servers, self.tools, self._mcp_stack)
            self._mcp_connected = True
        except Exception as e:
            logger.error(f"Failed to connect MCP servers (will retry next message): {e}")
            if self._mcp_stack:
                try:
                    await self._mcp_stack.aclose()
                except Exception:
                    pass
                self._mcp_stack = None
        finally:
            self._mcp_connecting = False

    async def close_mcp(self) -> None:
        """Close MCP server connections. Ported from HKUDS/nanobot v0.1.5."""
        if self._mcp_stack:
            try:
                await self._mcp_stack.aclose()
            except Exception:
                pass  # MCP SDK cancel scope cleanup is noisy but harmless
            self._mcp_stack = None
        self._mcp_connected = False

    def _register_default_tools(self) -> None:
        """Register default set of tools."""
        register_default_tools(
            registry=self.tools,
            config=self.config,
            send_callback=self.bus.publish_outbound,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
            include_spawn_tool=self._subagents_enabled(),
            include_viking_tools=self.config.ov_server.is_available(),
        )

    def _subagents_enabled(self) -> bool:
        agents_config = getattr(self.config, "agents", None)
        return bool(getattr(agents_config, "subagent_enabled", True))

    def _create_skill_runtime(
        self,
        *,
        session_key: SessionKey,
        workspace: Path,
        ov_tools_enable: bool,
        disabled_tools: list[str] | None,
        openviking_connection: dict[str, Any] | None,
        actor_peer_id: str | None,
    ) -> SkillRuntimeContext | None:
        if (
            self.config is None
            or not ov_tools_enable
            or not self.config.ov_server.is_available()
            or not self.tools.has("openviking_multi_read")
            or "openviking_multi_read" in set(disabled_tools or ())
        ):
            return None
        local_skills = SkillsLoader(workspace).list_skills(filter_unavailable=False)
        workspace_id = (
            self.sandbox_manager.to_workspace_id(session_key)
            if self.sandbox_manager
            else session_key.safe_name()
        )
        return SkillRuntimeContext(
            config=self.config,
            session_key=session_key,
            sandbox_manager=self.sandbox_manager,
            workspace_id=workspace_id,
            openviking_connection=openviking_connection,
            actor_peer_id=actor_peer_id,
            local_skill_names=(skill["name"] for skill in local_skills),
        )

    async def _execute_tool_turn(
        self,
        response: Any,
        messages: list[dict],
        request: _LoopRequestContext,
        *,
        active_tools: ToolRegistry,
        visible_tool_names: set[str],
        scoped_openviking_tools: set[str] | None,
        tools_used: list[dict],
        publish_events: bool,
        captured_turns: list[dict[str, Any]] | None,
        agent_id: str,
        iteration: int,
    ) -> tuple[list[dict], list[tuple[int, Any, ToolExecutionResult, float]]]:
        """Execute a model tool turn, preserving activation, event, and result order."""
        session_key = request.session_key
        skill_runtime = request.skill_runtime
        tool_call_dicts = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    # Unicode escapes inflate the history's character budget.
                    "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in response.tool_calls
        ]
        messages = self.context.add_assistant_message(
            messages,
            response.content,
            tool_call_dicts,
            reasoning_content=response.reasoning_content,
        )

        # Stage 2: activate Skills first, then execute remaining tools.
        async def execute_single_tool(idx: int, tool_call, allowed_names=visible_tool_names):
            """Execute a single tool and track execution time."""
            tool_execute_start_time = time.perf_counter()
            if tool_call.name not in allowed_names:
                result = f"Error: Tool '{tool_call.name}' is not available in this turn"
                return (
                    idx,
                    tool_call,
                    ToolExecutionResult(
                        result=result,
                        effective_params=dict(tool_call.arguments),
                    ),
                    0.0,
                )
            tool_connection = (
                request.openviking_connection
                if scoped_openviking_tools is None or tool_call.name in scoped_openviking_tools
                else None
            )
            execute_kwargs = {
                "session_key": session_key,
                "sandbox_manager": self.sandbox_manager,
                "sender_id": request.sender_id,
                "actor_peer_id": request.actor_peer_id or request.sender_id,
                "memory_peer_ids": request.memory_peer_ids,
                "memory_owner_user_ids": request.memory_owner_user_ids,
                "openviking_connection": tool_connection,
                "channel_metadata": request.channel_metadata,
            }
            if hasattr(active_tools, "execute_detailed"):
                outcome = await active_tools.execute_detailed(
                    tool_call.name,
                    tool_call.arguments,
                    **execute_kwargs,
                    skill_runtime=skill_runtime,
                )
            else:
                # Keep compatibility with embedders/tests that provide a
                # minimal registry implementing only the legacy API.
                result = await active_tools.execute(
                    tool_call.name, tool_call.arguments, **execute_kwargs
                )
                outcome = ToolExecutionResult(
                    result=result,
                    effective_params=dict(tool_call.arguments),
                )
            tool_execute_duration = (time.perf_counter() - tool_execute_start_time) * 1000
            return idx, tool_call, outcome, tool_execute_duration

        for tool_call in response.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info(
                "[TOOL_CALL]: {}({}) session={} agent={} iteration={} call_id={}",
                tool_call.name,
                args_str[:200],
                session_key.safe_name(),
                agent_id,
                iteration,
                tool_call.id,
            )
            if publish_events:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        session_key=session_key,
                        content=f"{tool_call.name}({args_str})",
                        event_type=OutboundEventType.TOOL_CALL,
                    )
                )

        indexed_calls = list(enumerate(response.tool_calls))
        activation_calls = []
        regular_calls = []
        for indexed_call in indexed_calls:
            _index, candidate_call = indexed_call
            if skill_runtime is not None and skill_runtime.is_activation_call(
                candidate_call.name, candidate_call.arguments
            ):
                activation_calls.append(indexed_call)
            else:
                regular_calls.append(indexed_call)

        # Complete Skill activation first. Calls emitted from the pre-activation
        # schema are never executed under the newly activated policy; the model
        # retries them on the next iteration with refreshed tool definitions.
        activation_results = await asyncio.gather(
            *(execute_single_tool(index, call) for index, call in activation_calls)
        )
        activation_failed = any(
            not _is_tool_result_success(item[2].result)
            or not skill_runtime.activation_succeeded(item[1].arguments)
            for item in activation_results
        )
        blocked_reason = None
        if activation_failed:
            blocked_reason = (
                "Error: SKILL_ACTIVATION_FAILED: execution was blocked "
                "because a Skill definition in this tool batch could not "
                "be activated"
            )
        elif activation_calls:
            blocked_reason = (
                "Error: SKILL_CONTEXT_UPDATED: one or more remote Skills "
                "were activated; retry this tool call using the refreshed "
                "tool definitions"
            )
        if blocked_reason is not None:
            regular_results = [
                (
                    index,
                    call,
                    ToolExecutionResult(
                        result=blocked_reason,
                        effective_params=dict(call.arguments),
                    ),
                    0.0,
                )
                for index, call in regular_calls
            ]
        else:
            regular_results = await asyncio.gather(
                *(execute_single_tool(index, call) for index, call in regular_calls)
            )
        results = sorted([*activation_results, *regular_results], key=lambda item: item[0])

        # Stage 3: Process results sequentially in original order
        turn_tools: list[dict[str, Any]] = []
        history_end = len(messages)
        request_media_bytes = _inline_media_bytes(messages)
        for _idx, tool_call, outcome, tool_execute_duration in results:
            result = outcome.result
            result_text = str(result)
            model_result = await self._preview_tool_result(
                result,
                session_key,
                preserve_complete=getattr(outcome, "preserve_complete_result", False),
            )
            recorded_result = (
                result_text if isinstance(result, MultimodalToolResult) else model_result
            )
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.info(f"[RESULT]: {result_text[:200]}")

            if publish_events:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        session_key=session_key,
                        content=str(model_result),
                        event_type=OutboundEventType.TOOL_RESULT,
                    )
                )
            if isinstance(result, MultimodalToolResult):
                result_media_bytes = _inline_media_bytes([{"content": result.content}])
                if not self.provider.should_inline_tool_result_media(self.model):
                    model_result = (
                        f"{result_text}\n\nMedia content was omitted by the configured "
                        "tool-result media policy."
                    )
                else:
                    overflow = (
                        request_media_bytes
                        + result_media_bytes
                        - MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
                    )
                    if overflow > 0:
                        reclaimed = _demote_historical_tool_media(
                            messages,
                            history_end=history_end,
                            bytes_needed=overflow,
                        )
                        request_media_bytes -= reclaimed
                    if (
                        request_media_bytes + result_media_bytes
                        > MAX_INLINE_TOOL_RESULT_MEDIA_BYTES
                    ):
                        model_result = (
                            f"{result_text}\n\nMedia content was omitted because adding it would "
                            "make this model request exceed the inline media "
                            f"limit of {MAX_INLINE_TOOL_RESULT_MEDIA_BYTES} bytes."
                        )
                    else:
                        request_media_bytes += result_media_bytes
            messages = self.context.add_tool_result(
                messages, tool_call.id, tool_call.name, model_result
            )

            tool_used_dict = {
                "tool_call_id": tool_call.id,
                "tool_name": tool_call.name,
                "args": args_str,
                "resolved_args": outcome.effective_params,
                "result": recorded_result,
                "duration": tool_execute_duration,
                "execute_success": _is_tool_result_success(result),
                "input_token": tool_call.tokens,
                "output_token": cal_str_tokens(result_text, text_type="mixed"),
            }
            if outcome.skill_uris:
                tool_used_dict["skill_uri"] = outcome.skill_uris[0]
                tool_used_dict["skill_uris"] = list(outcome.skill_uris)
            tools_used.append(tool_used_dict)
            turn_tools.append(tool_used_dict)

        if captured_turns is not None:
            captured_turns.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": turn_tools,
                    "timestamp": datetime.now().isoformat(),
                }
            )

        return messages, results

    async def run_structured_task(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        session_key: SessionKey,
        tool_registry: ToolRegistry,
        openviking_tool_names: list[str] | set[str],
        stop_tool_names: list[str],
        openviking_connection: dict[str, Any] | None,
        context_compact_budget: int | None = None,
        pre_compact_prompt: str | None = None,
        budget_reminder_thresholds: tuple[int, int, int] | None = None,
    ) -> tuple[Any, list[dict], dict[str, int], int]:
        """Run a tool-terminated structured task through the existing agent loop.

        ``budget_reminder_thresholds`` optionally adds an iteration countdown before
        model calls near the execution limit. ``pre_compact_prompt`` enables a bounded
        save turn before context compaction, using the task's existing file tools.
        """

        max_iterations = getattr(self, "max_iterations", 0)

        async def status_note_provider(iteration: int) -> str | None:
            remaining = max(0, max_iterations - iteration)
            if budget_reminder_thresholds and remaining in budget_reminder_thresholds:
                return render_budget_reminder(remaining, budget_reminder_thresholds)
            return None

        async def require_submission(context: _PlainTextContext) -> _PlainTextDelivered:
            messages = self.context.add_assistant_message(context.messages, context.text, [])
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your response was not submitted. Continue the task and call "
                        "submit_wiki_bundle once the complete final output is ready."
                    ),
                }
            )
            return _PlainTextDelivered(messages=messages, tools_used=[])

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        _content, _reasoning, tools_used, token_usage, iteration = await self._run_agent_loop(
            messages=messages,
            session_key=session_key,
            publish_events=False,
            ov_tools_enable=True,
            openviking_connection=openviking_connection,
            stop_tool_names=stop_tool_names,
            on_plain_text=require_submission,
            tool_registry=tool_registry,
            openviking_tool_names=openviking_tool_names,
            allow_final_fallback=False,
            inject_write_experience=False,
            context_compact_budget=context_compact_budget,
            pre_compact_prompt=pre_compact_prompt,
            status_note_provider=status_note_provider,
        )
        submit_tool = tool_registry.get("submit_wiki_bundle")
        bundle = getattr(submit_tool, "bundle", None)
        if bundle is None:
            if iteration >= self.max_iterations:
                raise AgentIterationLimitExceeded(self.max_iterations, usage=token_usage)
            raise ValueError("AGENT_OUTPUT_INVALID: Agent did not submit a valid Wiki bundle")
        return bundle, tools_used, token_usage, iteration
