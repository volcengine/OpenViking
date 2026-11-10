"""Main agent orchestration: dispatch messages, iterate the model, and persist replies."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from openviking.utils.media_limits import (
    MAX_INLINE_TOOL_RESULT_MEDIA_BYTES as MAX_INLINE_TOOL_RESULT_MEDIA_BYTES,
)
from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.loop.loop_compaction import (
    LoopCompactionMixin,
    _compact_msg_chars,
)
from vikingbot.agent.loop.loop_compaction import (
    _inline_media_bytes as _inline_media_bytes,
)
from vikingbot.agent.loop.loop_events import LoopEventMixin
from vikingbot.agent.loop.loop_session import (
    LoopSessionMixin,
    _ActiveTurn,
    _AssistantBoundary,
    _PreparedSteer,
)
from vikingbot.agent.loop.loop_tools import (
    AgentIterationLimitExceeded as AgentIterationLimitExceeded,
)
from vikingbot.agent.loop.loop_tools import (
    LoopToolMixin,
    _is_tool_result_success,
    _LoopRequestContext,
    _PlainTextContext,
    _PlainTextDelivered,
    _PlainTextFinal,
)
from vikingbot.agent.loop.loop_tools import render_budget_reminder as render_budget_reminder
from vikingbot.agent.remote_skills import SkillRuntimeContext
from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.events import InboundMessage, OutboundEventType, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import BotMode, Config, SessionKey
from vikingbot.heartbeat.service import HEARTBEAT_METADATA_KEY, is_heartbeat_noop_response
from vikingbot.hooks.manager import hook_manager
from vikingbot.integrations.langfuse import LangfuseClient
from vikingbot.providers.base import LLMProvider
from vikingbot.sandbox import SandboxManager
from vikingbot.session.manager import SessionManager
from vikingbot.utils.tracing import set_response_id, trace

if TYPE_CHECKING:
    from vikingbot.config.schema import ExecToolConfig
    from vikingbot.cron.service import CronService


class AgentLoop(LoopToolMixin, LoopEventMixin, LoopSessionMixin, LoopCompactionMixin):
    """
    The agent loop is the core processing engine.

    It:
    1. Receives messages from the bus
    2. Builds context with history, memory, skills
    3. Calls the LLM
    4. Executes tool calls
    5. Sends responses back
    """

    def __init__(
        self,
        bus: MessageBus,
        provider: LLMProvider,
        workspace: Path,
        model: str | None = None,
        temperature: float | None = None,
        max_iterations: int = 50,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exa_api_key: str | None = None,
        gen_image_model: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        session_manager: SessionManager | None = None,
        sandbox_manager: SandboxManager | None = None,
        config: Config = None,
        eval: bool = False,
        mcp_servers: dict | None = None,
    ):
        """
        Initialize the AgentLoop with all required dependencies and configuration.

        Args:
            bus: MessageBus instance for publishing and subscribing to messages.
            provider: LLMProvider instance for making LLM calls.
            workspace: Path to the workspace directory for file operations.
            model: Optional model identifier. If not provided, uses the provider's default.
            temperature: Sampling temperature; None uses the provider default.
            max_iterations: Maximum number of tool execution iterations per message (default: 50).
            memory_window: Maximum number of messages to keep in session memory (default: 50).
            brave_api_key: Optional API key for Brave search integration.
            exa_api_key: Optional API key for Exa search integration.
            gen_image_model: Optional model identifier for image generation (default: openai/doubao-seedream-4-5-251128).
            exec_config: Optional configuration for the exec tool (command execution).
            cron_service: Optional CronService for scheduled task management.
            session_manager: Optional SessionManager for session persistence. If not provided, a new one is created.
            sandbox_manager: Optional SandboxManager for sandboxed operations.
            config: Optional Config object with full configuration. Used if other parameters are not provided.

        Note:
            The AgentLoop creates its own ContextBuilder, SessionManager (if not provided),
            ToolRegistry, and SubagentManager during initialization.

        Example:
            >>> loop = AgentLoop(
            ...     bus=message_bus,
            ...     provider=llm_provider,
            ...     workspace=Path("/path/to/workspace"),
            ...     model="gpt-4",
            ...     max_iterations=30,
            ... )
        """
        from vikingbot.config.schema import ExecToolConfig  # noqa: F811

        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exa_api_key = exa_api_key
        self.gen_image_model = gen_image_model or "openai/doubao-seedream-4-5-251128"
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.sandbox_manager = sandbox_manager
        self.config = config

        self.context = ContextBuilder(
            workspace,
            sandbox_manager=sandbox_manager,
            enable_subagents=self._subagents_enabled(),
            config=self.config,
        )

        self._register_builtin_hooks()
        self.sessions = session_manager or SessionManager(
            self.config.bot_data_path, sandbox_manager=sandbox_manager
        )
        self.tools = ToolRegistry(config=self.config)
        self._eval = eval
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            config=self.config,
            model=self.model,
            temperature=self.temperature,
            sandbox_manager=sandbox_manager,
        )

        self._running = False
        self._mcp_servers = mcp_servers or {}
        self._mcp_stack: AsyncExitStack | None = None
        self._mcp_connected = False
        self._mcp_connecting = False
        self._ov_clients: dict[str, Any] = {}
        self._active_turn: _ActiveTurn | None = None
        self._queued_interrupt_counts: dict[SessionKey, int] = {}
        self._register_default_tools()

    def _register_builtin_hooks(self):
        """Register built-in hooks."""
        hook_manager.register_path(self.config.hooks)

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        while self._running:
            try:
                # Wait for next message
                msg = await self.bus.consume_inbound()

                # Process it
                try:
                    queued_interrupts = self._queued_interrupt_counts.get(msg.session_key, 0)
                    if queued_interrupts > 0:
                        if queued_interrupts == 1:
                            self._queued_interrupt_counts.pop(msg.session_key, None)
                        else:
                            self._queued_interrupt_counts[msg.session_key] = queued_interrupts - 1
                        response = await self._complete_interrupted_message(msg)
                    else:
                        response = await self._process_message_as_active_turn(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            session_key=msg.session_key,
                            content=f"Sorry, I encountered an error: {str(e)}",
                            metadata=msg.metadata,
                        )
                    )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _run_agent_loop(
        self,
        messages: list[dict],
        session_key: SessionKey,
        publish_events: bool = True,
        sender_id: str | None = None,
        actor_peer_id: str | None = None,
        ov_tools_enable: bool = True,
        memory_peer_ids: list[str] | None = None,
        memory_owner_user_ids: list[str] | None = None,
        disabled_tools: list[str] | None = None,
        openviking_connection: dict[str, Any] | None = None,
        stop_tool_names: list[str] | None = None,
        on_plain_text: Any | None = None,
        channel_metadata: dict[str, Any] | None = None,
        captured_turns: list[dict[str, Any]] | None = None,
        tool_registry: ToolRegistry | None = None,
        openviking_tool_names: list[str] | set[str] | None = None,
        allow_final_fallback: bool = True,
        inject_write_experience: bool = True,
        context_compact_budget: int | None = None,
        pre_compact_prompt: str | None = None,
        status_note_provider: Any | None = None,
        should_stop: Callable[[], bool] | None = None,
        skill_runtime: Any | None = None,
        active_turn: _ActiveTurn | None = None,
        agent_id: str = "main",
        max_iterations: int | None = None,
    ) -> tuple[str | None, str | None, list[dict], dict[str, int], int]:
        """
        Run the core agent loop: call LLM, execute tools, repeat until done.

        Args:
            messages: Initial message list
            session_key: Session key for tool execution context
            publish_events: Whether to publish ITERATION/REASONING/TOOL_CALL events to the bus
            sender_id: Sender identity forwarded to the tool execution context
            actor_peer_id: Authenticated OpenViking peer identity for tools
            ov_tools_enable: Whether to enable OpenViking tools for this session
            memory_peer_ids: List of peer IDs for memory retrieval
            memory_owner_user_ids: List of explicit OpenViking user IDs for
                trusted-mode owner-user memory lookup
            disabled_tools: Tool names to hide from the model for this request
            openviking_connection: Request-scoped OpenViking identity for tools
            stop_tool_names: Tool names that terminate the loop immediately after execution
            on_plain_text: Optional async callback invoked when the model returns a non-empty
                plain-text reply (no tool calls). It receives (messages, text, iteration) and
                must return a PlainTextRouteResult to either deliver the text and continue the
                loop (e.g. forward it to a user simulator) or treat it as the final reply. When
                None, plain text is treated as the final assistant reply (default chatbot
                semantics).
            channel_metadata: Channel-specific metadata for tools that publish outbound messages
            captured_turns: Optional mutable list populated with each intermediate assistant
                turn and the tool calls/results produced by that same model response. Reasoning
                content is intentionally excluded.
            tool_registry: Optional request-scoped registry used for tool definitions and
                execution. Defaults to the agent's shared tool registry.
            openviking_tool_names: Optional collection of tool names allowed to receive the
                request-scoped OpenViking connection. None preserves the legacy behavior of
                forwarding the connection to all tools; an empty collection forwards it to none.
            allow_final_fallback: Whether to make a final tool-free LLM call after the
                tool-use iteration limit is reached.
            inject_write_experience: Whether to retrieve and inject relevant agent experience
                before executing configured write tools.
            pre_compact_prompt: Optional one-turn save instruction at 85% of the character
                budget. Uses existing tools and iteration budget; a successful stop tool
                ends the loop before compaction. Already oversized contexts compact
                immediately; saving is best effort.
            status_note_provider: Optional async callback ``(iteration) -> str | None``.
                When set, its result is appended to the model-facing messages right before
                every model call. Compile emits a reminder at each configured iteration
                threshold; ordinary chat leaves it ``None``.
            should_stop: Optional synchronous callback checked before each iteration.
                Returning true ends the loop without an extra model call.
            agent_id: Diagnostic identity for model and tool logs; Compile children use
                their child task ID while sharing the parent's session and workspace.
            max_iterations: Positive budget for this invocation, or None to use the
                instance default. Concurrent children do not change each other's budgets.

        Returns:
            tuple of (final_content, final_reasoning_content, tools_used, token_usage, iteration)
        """
        iteration_limit = self.max_iterations if max_iterations is None else max_iterations
        if max_iterations is not None and max_iterations < 1:
            raise ValueError("max_iterations must be positive")
        iteration = 0
        active_tools = tool_registry or self.tools
        scoped_openviking_tools = (
            set(openviking_tool_names) if openviking_tool_names is not None else None
        )
        request = _LoopRequestContext(
            session_key=session_key,
            sender_id=sender_id,
            actor_peer_id=actor_peer_id,
            memory_peer_ids=memory_peer_ids,
            memory_owner_user_ids=memory_owner_user_ids,
            openviking_connection=openviking_connection,
            channel_metadata=channel_metadata,
            skill_runtime=skill_runtime,
        )
        final_content = None
        final_reasoning_content = None
        tools_used: list[dict] = []
        token_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        write_exp_injected = False
        stop_tools = set(stop_tool_names or [])
        compact_pending = False
        compact_threshold_chars = (context_compact_budget or 0) * 0.85
        compact_trigger_chars = compact_threshold_chars
        steer_memory_events: list[_PreparedSteer] = []

        def accumulate_token_usage(response: Any) -> None:
            if not response.usage:
                return
            cur_token = response.usage
            token_usage["prompt_tokens"] += cur_token.get("prompt_tokens", 0)
            token_usage["completion_tokens"] += cur_token.get("completion_tokens", 0)
            token_usage["total_tokens"] += cur_token.get("total_tokens", 0)
            token_usage["cache_read_input_tokens"] += cur_token.get("cache_read_input_tokens", 0)

        while iteration < iteration_limit:
            if should_stop is not None and should_stop():
                break
            iteration += 1

            if active_turn is not None and iteration >= iteration_limit:
                active_turn.accepting_steer = False
            accepted_steers = await self._take_accepted_steers(active_turn)
            for steer in accepted_steers:
                messages.extend(steer.prompt_messages)
            if accepted_steers:
                active_turn.history_items.extend(accepted_steers)
            steer_memory_events.extend(accepted_steers)

            if context_compact_budget is not None or _compact_msg_chars(messages) > 240_000:
                budget = context_compact_budget or 240_000
                current_chars = _compact_msg_chars(messages)
                if compact_pending or current_chars > budget:
                    messages = await self._compact_tool_loop(
                        messages, session_key, budget_chars=budget
                    )
                    compact_pending = False
                    # Fixed task content can exceed the save threshold; wait for new content.
                    compact_trigger_chars = max(
                        compact_threshold_chars, _compact_msg_chars(messages)
                    )
                elif (
                    pre_compact_prompt
                    and 1 < iteration < iteration_limit
                    and current_chars > compact_trigger_chars
                ):
                    messages.append({"role": "user", "content": pre_compact_prompt})
                    compact_pending = True

            if publish_events:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        session_key=session_key,
                        content=f"Iteration {iteration}/{iteration_limit}",
                        event_type=OutboundEventType.ITERATION,
                    )
                )
                for steer in steer_memory_events:
                    if steer.auto_memory_tool:
                        await self._publish_auto_memory_context(
                            session_key=session_key,
                            query=steer.msg.content,
                            result=steer.auto_memory_tool["result"],
                        )
            steer_memory_events.clear()

            if status_note_provider is not None:
                note = await status_note_provider(iteration)
                if note:
                    messages.append({"role": "user", "content": note})

            tool_definitions = active_tools.get_definitions(
                ov_tools_enable=ov_tools_enable,
                disabled_tools=disabled_tools,
                skill_runtime=skill_runtime,
            )
            budget = context_compact_budget or 240_000
            if _compact_msg_chars(messages) > budget:
                messages = await self._compact_tool_loop(messages, session_key, budget_chars=budget)
            visible_tool_names = {
                str(definition.get("function", {}).get("name") or "")
                for definition in tool_definitions
                if isinstance(definition, dict)
            }
            response, _streamed_content, streamed_reasoning = await self._chat_with_stream_events(
                messages=messages,
                tools=tool_definitions,
                session_key=session_key,
                publish_events=publish_events,
                agent_id=agent_id,
                iteration=iteration,
            )
            accumulate_token_usage(response)

            if publish_events and response.reasoning_content and not streamed_reasoning:
                await self.bus.publish_outbound(
                    OutboundMessage(
                        session_key=session_key,
                        content=response.reasoning_content,
                        event_type=OutboundEventType.REASONING,
                    )
                )

            if response.has_tool_calls:
                # Inject experience memory before write-related tool calls (once per session)
                if inject_write_experience and not write_exp_injected:
                    _ov_cfg = self.config.ov_server
                    _write_tools = set(_ov_cfg.exp_write_tools)
                    if any(tc.name in _write_tools for tc in response.tool_calls):
                        write_exp_injected = True
                        try:
                            # Build query from last 3 user messages
                            _user_msgs = [
                                m["content"]
                                for m in messages
                                if m.get("role") == "user" and isinstance(m.get("content"), str)
                            ]
                            _query = "\n".join(_user_msgs[-3:])
                            workspace_id = (
                                self.sandbox_manager.to_workspace_id(session_key)
                                if self.sandbox_manager
                                else "shared"
                            )
                            _exp = await self.context.memory.get_viking_experience_context(
                                query=_query,
                                workspace_id=workspace_id,
                                openviking_connection=openviking_connection,
                            )
                            logger.info(
                                f"[WRITE_EXP]: write tool detected, exp_found={bool(_exp)}, query={_query[:50]}"
                            )
                            if _exp:
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": f"## Relevant Agent Experience\n{_exp}",
                                    }
                                )
                                continue
                        except Exception as _e:
                            logger.warning(f"[WRITE_EXP]: failed to load experience: {_e}")

                final_reasoning_content = response.reasoning_content
                messages, results = await self._execute_tool_turn(
                    response,
                    messages,
                    request,
                    active_tools=active_tools,
                    visible_tool_names=visible_tool_names,
                    scoped_openviking_tools=scoped_openviking_tools,
                    tools_used=tools_used,
                    publish_events=publish_events,
                    captured_turns=captured_turns,
                    agent_id=agent_id,
                    iteration=iteration,
                )
                if any(
                    tool_call.name in stop_tools and _is_tool_result_success(_outcome.result)
                    for _idx, tool_call, _outcome, _duration in results
                ):
                    final_content = ""
                    break

            else:
                text = (response.content or "").strip()
                if text and on_plain_text is not None:
                    try:
                        route = await on_plain_text(
                            _PlainTextContext(
                                messages=messages,
                                session_key=session_key,
                                text=text,
                                reasoning_content=response.reasoning_content,
                                iteration=iteration,
                                tools=active_tools,
                                sandbox_manager=self.sandbox_manager,
                                sender_id=sender_id,
                                memory_peer_ids=memory_peer_ids,
                                memory_owner_user_ids=memory_owner_user_ids,
                                openviking_connection=openviking_connection,
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "[PLAIN_TEXT_HOOK]: hook raised; treating as final answer: %s", exc
                        )
                        route = None
                    if isinstance(route, _PlainTextDelivered):
                        messages = route.messages
                        tools_used.extend(route.tools_used)
                        if captured_turns is not None:
                            captured_turns.append(
                                {
                                    "role": "assistant",
                                    "content": text,
                                    "tool_calls": list(route.tools_used),
                                    "timestamp": datetime.now().isoformat(),
                                }
                            )
                        if route.user_terminates:
                            final_content = ""
                            break
                        messages.append(
                            {
                                "role": "user",
                                "content": "Reflect on the results and decide next steps.",
                            }
                        )
                        continue
                    if isinstance(route, _PlainTextFinal):
                        final_content = route.content if route.content is not None else text
                        final_reasoning_content = response.reasoning_content
                        break
                    # Unknown / None result: fall through to default final-answer handling.
                if not text and on_plain_text is not None:
                    # Structured tasks treat an empty model response as a
                    # transient hiccup, not a final answer: nudge and continue.
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "Your response was empty. Continue the task and call the "
                                "designated final submission tool with the complete bundle."
                            ),
                        }
                    )
                    continue
                final_steers = await self._take_accepted_steers(active_turn)
                if final_steers:
                    messages = self.context.add_assistant_message(
                        messages,
                        response.content,
                        [],
                        reasoning_content=response.reasoning_content,
                    )
                    active_turn.history_items.append(
                        _AssistantBoundary(
                            content=response.content or "",
                            reasoning_content=response.reasoning_content,
                        )
                    )
                    active_turn.history_items.extend(final_steers)
                    for steer in final_steers:
                        messages.extend(steer.prompt_messages)
                    steer_memory_events.extend(final_steers)
                    continue
                if active_turn is not None:
                    # No await separates this close from the empty queue check,
                    # so a successful steer cannot slip past the final checkpoint.
                    active_turn.accepting_steer = False
                final_content = response.content
                final_reasoning_content = response.reasoning_content
                break

        if final_content == "" and any(t.get("tool_name") in stop_tools for t in tools_used):
            pass
        elif final_content is None or (
            isinstance(final_content, str) and not final_content.strip()
        ):
            if iteration >= iteration_limit and allow_final_fallback:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tool-use iteration limit reached. Do not call any more tools. "
                            "Answer the user's original request directly using only the "
                            "conversation, tool calls, and tool results already available above. "
                            "If the gathered information is incomplete, explain the best-known "
                            "answer and clearly note what remains uncertain."
                        ),
                    }
                )
                budget = context_compact_budget or 240_000
                if _compact_msg_chars(messages) > budget:
                    messages = await self._compact_tool_loop(
                        messages, session_key, budget_chars=budget
                    )
                (
                    response,
                    _streamed_content,
                    streamed_reasoning,
                ) = await self._chat_with_stream_events(
                    messages=messages,
                    tools=[],
                    session_key=session_key,
                    publish_events=publish_events,
                    agent_id=agent_id,
                    iteration=iteration + 1,
                )
                accumulate_token_usage(response)
                final_content = response.content
                final_reasoning_content = response.reasoning_content

                if publish_events and response.reasoning_content and not streamed_reasoning:
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            session_key=session_key,
                            content=response.reasoning_content,
                            event_type=OutboundEventType.REASONING,
                        )
                    )

        if final_content is None or (
            isinstance(final_content, str) and not final_content.strip()
            and not any(t.get("tool_name") in stop_tools for t in tools_used)
        ):
            if iteration >= iteration_limit:
                final_content = (
                    "I reached the tool-use limit before completing every step, and the "
                    "available tool results are not enough for a reliable final answer."
                )
            else:
                final_content = "I've completed processing but have no response to give."

        return final_content, final_reasoning_content, tools_used, token_usage, iteration

    @trace(
        name="process_message",
        extract_session_id=lambda msg: msg.session_key.safe_name(),
        extract_user_id=lambda msg: msg.sender_id,
    )
    async def _process_message(
        self,
        msg: InboundMessage,
        active_turn: _ActiveTurn | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).

        Returns:
            The response message, or None if no response needed.
        """
        # Handle system messages (subagent announces)
        # The chat_id contains the original "channel:chat_id" to route back to
        start_time = time.time()
        long_running_notified = False

        # 监控处理时长，每50秒发送处理中提示事件
        async def check_long_running():
            nonlocal long_running_notified
            tick_count = 0
            # 最多发送7次提示
            max_ticks = 7

            while not long_running_notified and tick_count < max_ticks:
                await asyncio.sleep(60)
                if long_running_notified:
                    break
                if msg.metadata:
                    message_id = msg.metadata.get("message_id")
                    if message_id:
                        try:
                            # 发送处理中tick事件，对应channel会自行处理展示逻辑
                            await self.bus.publish_outbound(
                                OutboundMessage(
                                    session_key=msg.session_key,
                                    content="",
                                    metadata={
                                        "action": "processing_tick",
                                        "tick_count": tick_count,
                                        "message_id": message_id,
                                    },
                                )
                            )
                            tick_count += 1
                        except Exception as e:
                            logger.debug(f"Failed to send processing tick: {e}")

        monitor_task = asyncio.create_task(check_long_running())
        skill_runtime: SkillRuntimeContext | None = None

        try:
            if msg.session_key.type == "system":
                return await self._process_system_message(msg)

            preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
            logger.info(f"Processing message from {msg.session_key}:{msg.sender_id}: {preview}")

            session_key = msg.session_key
            # For CLI/direct sessions, skip heartbeat by default
            skip_heartbeat = session_key.type == "cli"
            session = self.sessions.get_or_create(session_key, skip_heartbeat=skip_heartbeat)

            ov_tools_enable = self._get_ov_tools_enable(session_key)
            disabled_tools = msg.metadata.get("disabled_tools", []) if msg.metadata else []
            if not isinstance(disabled_tools, list):
                disabled_tools = []
            if msg.metadata.get("studio_managed"):
                from vikingbot.studio.policy import disabled_group_tools

                disabled_tools = list(
                    set(disabled_tools) | set(disabled_group_tools(self.tools.tool_names))
                )
            message_options = self._resolve_message_options(msg, ov_tools_enable=ov_tools_enable)
            openviking_connection = message_options.openviking_connection
            msg.openviking_connection = openviking_connection
            actor_peer_id = message_options.actor_peer_id
            msg.actor_peer_id = actor_peer_id
            profile_user_list = message_options.profile_user_list
            memory_peer_ids = message_options.memory_peer_ids
            memory_owner_user_ids = message_options.memory_owner_user_ids

            # Handle slash commands
            is_group_chat = msg.metadata.get("chat_type") == "group" if msg.metadata else False
            command_response = await self._handle_session_command(
                msg,
                session,
                ov_tools_enable,
                openviking_connection,
                is_group_chat=is_group_chat,
            )
            if command_response is not None:
                return command_response

            # Debug mode handling
            if self.config.mode == BotMode.DEBUG:
                # In debug mode, only record message to session, no processing or reply
                await self._evaluate_previous_response_outcome(session, msg)
                session.add_message("user", msg.content, sender_id=msg.sender_id)
                await self.sessions.save(session)
                return None

            if not msg.need_reply:
                await self._evaluate_previous_response_outcome(session, msg)
                session.add_message("user", msg.content, sender_id=msg.sender_id)
                await self.sessions.save(session)
                return OutboundMessage(
                    session_key=msg.session_key,
                    content="",
                    metadata=msg.metadata,
                    event_type=OutboundEventType.NO_REPLY,
                )

            outcome_update = await self._evaluate_previous_response_outcome(session, msg)

            session_policy = self._get_session_policy()
            if not self._eval:
                await session_policy.before_turn(session, msg, outcome_update=outcome_update)

            if self.sandbox_manager:
                message_workspace = self.sandbox_manager.get_workspace_path(session_key)
            else:
                message_workspace = self.workspace

            skill_runtime = self._create_skill_runtime(
                session_key=session_key,
                workspace=message_workspace,
                ov_tools_enable=ov_tools_enable,
                disabled_tools=disabled_tools,
                openviking_connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )
            remote_skills_summary = ""
            if skill_runtime is not None:
                await skill_runtime.discover(msg.content)
                remote_skills_summary = skill_runtime.build_discovery_summary()

            from vikingbot.agent.context import ContextBuilder

            message_context = ContextBuilder(
                message_workspace,
                sandbox_manager=self.sandbox_manager,
                sender_id=msg.sender_id,
                actor_peer_id=actor_peer_id,
                sender_name=msg.sender_name,
                is_group_chat=is_group_chat,
                eval=self._eval,
                openviking_connection=openviking_connection,
                remote_skills_summary=remote_skills_summary,
                enable_subagents=self._subagents_enabled(),
                config=self.config,
            )

            # Build initial messages (use OpenViking session context when enabled)
            provider_name = self.config.get_provider_name(self.model) if self.config else None
            history = await self._build_prompt_history(
                session,
                provider_name=provider_name,
                openviking_connection=openviking_connection,
                actor_peer_id=actor_peer_id,
            )

            # Experience recall deduplication: URIs already recalled in this session
            # are stored in session.metadata and survive restarts (persisted to JSONL).
            exp_exclude_uris = session.metadata.get("recalled_exp_uris", [])
            if not isinstance(exp_exclude_uris, list):
                exp_exclude_uris = []

            messages = await message_context.build_messages(
                history=history,
                current_message=msg.content,
                media=msg.media if msg.media else None,
                session_key=msg.session_key,
                ov_tools_enable=ov_tools_enable,
                profile_user_list=profile_user_list,
                memory_peer_ids=memory_peer_ids,
                memory_owner_user_ids=memory_owner_user_ids,
                exp_exclude_uris=exp_exclude_uris,
            )
            relevant_memories = message_context.latest_relevant_memories
            auto_memory_tool = None
            if ov_tools_enable and relevant_memories:
                auto_memory_tool = await self._publish_auto_memory_context(
                    session_key=session_key,
                    query=msg.content,
                    result=relevant_memories,
                )

            # Track newly recalled experience URIs for deduplication
            newly_recalled_exp_uris = getattr(message_context, "latest_recalled_exp_uris", [])
            newly_recalled_exp_content = getattr(message_context, "latest_recalled_exp_content", "")

            # Run agent loop within a stable response identity for tracing/tool spans.
            response_id = uuid.uuid4().hex
            agent_turns: list[dict[str, Any]] = []
            with set_response_id(response_id):
                (
                    final_content,
                    final_reasoning_content,
                    tools_used,
                    token_usage,
                    iteration,
                ) = await self._run_agent_loop(
                    messages=messages,
                    session_key=session_key,
                    publish_events=True,
                    sender_id=msg.sender_id,
                    actor_peer_id=actor_peer_id,
                    ov_tools_enable=ov_tools_enable,
                    memory_peer_ids=memory_peer_ids,
                    memory_owner_user_ids=memory_owner_user_ids,
                    disabled_tools=disabled_tools,
                    openviking_connection=openviking_connection,
                    channel_metadata=msg.metadata,
                    captured_turns=agent_turns,
                    skill_runtime=skill_runtime,
                    active_turn=active_turn,
                )

            if auto_memory_tool:
                tools_used = [auto_memory_tool, *(tools_used or [])]
            active_turn_history = list(active_turn.history_items) if active_turn is not None else []
            for item in active_turn_history:
                if isinstance(item, _PreparedSteer) and item.auto_memory_tool:
                    tools_used.append(item.auto_memory_tool)

            # Log response preview
            preview = final_content[:300] + "..." if len(final_content) > 300 else final_content
            logger.info(f"Response to {msg.session_key}: {preview}")

            response_completed = self._build_response_completed_payload(
                msg=msg,
                response_id=response_id,
                final_content=final_content,
                final_reasoning_content=final_reasoning_content,
                token_usage=token_usage,
                time_cost_seconds=time.time() - start_time,
                iteration=iteration,
                tools_used=tools_used,
            )

            if active_turn is not None:
                # Detach before persistence so a later interrupt cannot cancel final session writes.
                self._active_turn = None

            is_heartbeat = bool(msg.metadata.get(HEARTBEAT_METADATA_KEY))
            if not (is_heartbeat and is_heartbeat_noop_response(final_content)):
                if newly_recalled_exp_uris:
                    recalled_uris = session.metadata.setdefault("recalled_exp_uris", [])
                    for uri in newly_recalled_exp_uris:
                        if uri not in recalled_uris:
                            recalled_uris.append(uri)
                # Write experience reminder into session history so it persists
                # across turns and survives bot restarts (in JSONL).
                if newly_recalled_exp_content:
                    session.add_message(
                        "user",
                        f"[Experience Reminder]\n## Relevant Agent Experience\n{newly_recalled_exp_content}",
                        exp_uris=newly_recalled_exp_uris,
                        experience_reminder=True,
                    )
                session.add_message("user", msg.content, sender_id=msg.sender_id)
                self._append_active_turn_history(session, active_turn_history)
                session.add_message(
                    "assistant",
                    final_content,
                    response_id=response_id,
                    tools_used=tools_used if tools_used else None,
                    token_usage=token_usage,
                    sender_id=msg.sender_id,
                    reasoning_content=final_reasoning_content,
                    agent_turns=agent_turns if agent_turns else None,
                )
                session.metadata.setdefault("response_facts", {})[response_id] = response_completed
                await self.sessions.save(session)
                if not self._eval:
                    await session_policy.after_turn(session, openviking_connection)
            LangfuseClient.get_instance().update_generation_metadata(
                response_id, response_completed
            )
            response_metadata = dict(msg.metadata or {})
            if relevant_memories is not None:
                response_metadata["relevant_memories"] = relevant_memories
            await self.bus.publish_outbound(
                OutboundMessage(
                    session_key=msg.session_key,
                    content="",
                    event_type=OutboundEventType.RESPONSE_COMPLETED,
                    response_id=response_id,
                    metadata={"response_completed": response_completed},
                )
            )
            return OutboundMessage(
                session_key=msg.session_key,
                content=final_content,
                metadata=response_metadata,
                response_id=response_id,
                token_usage=token_usage,
                time_cost=response_completed["time_cost_ms"] / 1000,
                iteration=response_completed["iteration_count"],
                tools_used_names=response_completed["tools_used_names"],
            )
        finally:
            if skill_runtime is not None:
                try:
                    await skill_runtime.close()
                except Exception as exc:
                    logger.warning("Failed to close remote Skill runtime: {}", exc)
            long_running_notified = True
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

    async def process_direct(
        self,
        content: str,
        session_key: SessionKey = SessionKey(type="cli", channel_id="default", chat_id="direct"),
        metadata: dict[str, object] | None = None,
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).

        Returns:
            The agent's response.
        """
        await self._connect_mcp()
        msg = InboundMessage(
            session_key=session_key,
            sender_id="user",
            content=content,
            metadata=metadata or {},
        )

        response = await self._process_message(msg)
        return response.content if response else ""
