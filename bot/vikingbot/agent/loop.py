"""Agent loop: the core processing engine."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from loguru import logger

from vikingbot.agent.context import ContextBuilder
from vikingbot.agent.memory import MemoryStore
from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools import register_default_tools
from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.events import InboundMessage, OutboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import Config
from vikingbot.config.schema import SessionKey
from vikingbot.hooks import HookContext
from vikingbot.hooks.manager import hook_manager
from vikingbot.providers.base import LLMProvider
from vikingbot.sandbox import SandboxManager
from vikingbot.session.manager import SessionManager
from vikingbot.utils.helpers import cal_str_tokens


class ThinkingStepType(Enum):
    """思考步骤类型（简化版本，避免循环依赖）"""

    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ITERATION = "iteration"


@dataclass
class ThinkingStep:
    """单个思考步骤（简化版本，避免循环依赖）"""

    step_type: ThinkingStepType
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


class AgentLoop:
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
        max_iterations: int = 50,
        memory_window: int = 50,
        brave_api_key: str | None = None,
        exa_api_key: str | None = None,
        gen_image_model: str | None = None,
        exec_config: "ExecToolConfig | None" = None,
        cron_service: "CronService | None" = None,
        session_manager: SessionManager | None = None,
        sandbox_manager: SandboxManager | None = None,
        thinking_callback=None,
        config: Config = None,
    ):
        from vikingbot.config.schema import ExecToolConfig

        self.bus = bus
        self.provider = provider
        self.workspace = workspace
        self.model = model or provider.get_default_model()
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.brave_api_key = brave_api_key
        self.exa_api_key = exa_api_key
        self.gen_image_model = gen_image_model or "openai/doubao-seedream-4-5-251128"
        self.exec_config = exec_config or ExecToolConfig()
        self.cron_service = cron_service
        self.sandbox_manager = sandbox_manager
        self.config = config

        self.context = ContextBuilder(workspace, sandbox_manager=sandbox_manager)

        self._register_builtin_hooks()
        self.sessions = session_manager or SessionManager(
            workspace, sandbox_manager=sandbox_manager
        )
        self.tools = ToolRegistry()
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            config=self.config,
            model=self.model,
            sandbox_manager=sandbox_manager,
        )

        self._running = False
        self.thinking_callback = thinking_callback
        self._register_default_tools()

    def _register_builtin_hooks(self):
        """Register built-in hooks."""
        hook_manager.register_path(self.config.hooks)

    def _register_default_tools(self) -> None:
        """Register default set of tools."""
        register_default_tools(
            registry=self.tools,
            config=self.config,
            send_callback=self.bus.publish_outbound,
            subagent_manager=self.subagents,
            cron_service=self.cron_service,
        )

    async def run(self) -> None:
        """Run the agent loop, processing messages from the bus."""
        self._running = True
        logger.info("Agent loop started")

        while self._running:
            try:
                # Wait for next message
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)

                # Process it
                try:
                    response = await self._process_message(msg)
                    if response:
                        await self.bus.publish_outbound(response)
                except Exception as e:
                    logger.exception(f"Error processing message: {e}")
                    # Send error response
                    await self.bus.publish_outbound(
                        OutboundMessage(
                            session_key=msg.session_key,
                            content=f"Sorry, I encountered an error: {str(e)}",
                        )
                    )
            except asyncio.TimeoutError:
                continue

    def stop(self) -> None:
        """Stop the agent loop."""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(self, msg: InboundMessage) -> OutboundMessage | None:
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
        if msg.session_key.type == "system":
            return await self._process_system_message(msg)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info(f"Processing message from {msg.session_key}:{msg.sender_id}: {preview}")

        # Get or create session
        session_key = msg.session_key
        # For CLI/direct sessions, skip heartbeat by default
        skip_heartbeat = session_key.type in ("cli", "tui")
        session = self.sessions.get_or_create(session_key, skip_heartbeat=skip_heartbeat)

        # Handle slash commands
        cmd = msg.content.strip().lower()
        if cmd == "/new":
            await self._consolidate_memory(session, archive_all=True)
            session.clear()
            self.sessions.save(session)
            return OutboundMessage(
                session_key=msg.session_key, content="🐈 New session started. Memory consolidated."
            )
        if cmd == "/help":
            return OutboundMessage(
                session_key=msg.session_key,
                content="🐈 vikingbot commands:\n/new — Start a new conversation\n/help — Show available commands",
            )

        # Consolidate memory before processing if session is too large
        if len(session.messages) > self.memory_window:
            await self._consolidate_memory(session)

        if self.sandbox_manager:
            message_workspace = self.sandbox_manager.get_workspace_path(session_key)
        else:
            message_workspace = self.workspace

        from vikingbot.agent.context import ContextBuilder

        message_context = ContextBuilder(message_workspace, sandbox_manager=self.sandbox_manager)

        # Build initial messages (use get_history for LLM-formatted messages)
        messages = await message_context.build_messages(
            history=session.get_history(),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            session_key=msg.session_key,
        )

        # Agent loop
        iteration = 0
        final_content = None
        tools_used: list[dict] = []

        while iteration < self.max_iterations:
            iteration += 1

            # 回调：迭代开始
            if self.thinking_callback:
                self.thinking_callback(
                    ThinkingStep(
                        step_type=ThinkingStepType.ITERATION,
                        content=f"Iteration {iteration}/{self.max_iterations}",
                        metadata={"iteration": iteration},
                    )
                )

            # Call LLM
            response = await self.provider.chat(
                messages=messages, tools=self.tools.get_definitions(), model=self.model
            )

            # 回调：推理内容
            if response.reasoning_content and self.thinking_callback:
                self.thinking_callback(
                    ThinkingStep(
                        step_type=ThinkingStepType.REASONING,
                        content=response.reasoning_content,
                        metadata={},
                    )
                )

            # Handle tool calls
            if response.has_tool_calls:
                args_list = [tc.arguments for tc in response.tool_calls]
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(args),  # Use truncated args
                        },
                    }
                    for tc, args in zip(response.tool_calls, args_list)
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                # Execute tools
                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)

                    # 回调：工具调用
                    if self.thinking_callback:
                        self.thinking_callback(
                            ThinkingStep(
                                step_type=ThinkingStepType.TOOL_CALL,
                                content=f"{tool_call.name}({args_str})",
                                metadata={"tool": tool_call.name, "args": tool_call.arguments},
                            )
                        )

                    logger.info(f"[TOOL_CALL]: {tool_call.name}({args_str[:200]})")
                    tool_execute_start_time = time.time()
                    result = await self.tools.execute(
                        tool_call.name,
                        tool_call.arguments,
                        session_key=session_key,
                        sandbox_manager=self.sandbox_manager,
                    )
                    tool_execute_duration = (time.time() - tool_execute_start_time) * 1000
                    logger.info(f"[RESULT]: {str(result)[:600]}")

                    # 回调：工具结果
                    if self.thinking_callback:
                        result_str = str(result)
                        if len(result_str) > 500:
                            result_str = result_str[:500] + "..."
                        self.thinking_callback(
                            ThinkingStep(
                                step_type=ThinkingStepType.TOOL_RESULT,
                                content=result_str,
                                metadata={"tool": tool_call.name},
                            )
                        )

                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )

                    tool_used_dict = {
                        "tool_name": tool_call.name,
                        "args": args_str,
                        "result": result,
                        "duration": tool_execute_duration,
                        "execute_success": True
                        if result and "Error executing" not in result
                        else False,
                        "input_token": tool_call.tokens,
                        "output_token": cal_str_tokens(result, text_type="mixed"),
                    }
                    tools_used.append(tool_used_dict)
                # Interleaved CoT: reflect before next action
                messages.append(
                    {"role": "user", "content": "Reflect on the results and decide next steps."}
                )
            else:
                # No tool calls, we're done
                final_content = response.content
                break

        if final_content is None:
            if iteration >= self.max_iterations:
                final_content = f"Reached {self.max_iterations} iterations without completion."
            else:
                final_content = "I've completed processing but have no response to give."

        # Log response preview
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info(f"Response to {msg.session_key}: {preview}")

        # Save to session (include tool names so consolidation sees what happened)
        session.add_message("user", msg.content)
        session.add_message(
            "assistant", final_content, tools_used=tools_used if tools_used else None
        )
        self.sessions.save(session)

        return OutboundMessage(
            session_key=msg.session_key,
            content=final_content,
            metadata=msg.metadata
            or {},  # Pass through for channel-specific needs (e.g. Slack thread_ts)
        )

    async def _process_system_message(self, msg: InboundMessage) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        session = self.sessions.get_or_create(msg.session_key)

        # Build messages with the announce content
        messages = await self.context.build_messages(
            history=session.get_history(), current_message=msg.content, session_key=msg.session_key
        )

        # Agent loop (limited for announce handling)
        iteration = 0
        final_content = None

        while iteration < self.max_iterations:
            iteration += 1

            response = await self.provider.chat(
                messages=messages, tools=self.tools.get_definitions(), model=self.model
            )

            if response.has_tool_calls:
                tool_call_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                    }
                    for tc in response.tool_calls
                ]
                messages = self.context.add_assistant_message(
                    messages,
                    response.content,
                    tool_call_dicts,
                    reasoning_content=response.reasoning_content,
                )

                for tool_call in response.tool_calls:
                    args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
                    logger.info(f"Tool call: {tool_call.name}({args_str[:200]})")
                    result = await self.tools.execute(
                        tool_call.name,
                        tool_call.arguments,
                        session_key=msg.session_key,
                        sandbox_manager=self.sandbox_manager,
                    )
                    messages = self.context.add_tool_result(
                        messages, tool_call.id, tool_call.name, result
                    )
                # Interleaved CoT: reflect before next action
                messages.append(
                    {"role": "user", "content": "Reflect on the results and decide next steps."}
                )
            else:
                final_content = response.content
                break

        if final_content is None:
            final_content = "Background task completed."

        # Save to session (mark as system message in history)
        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        session.add_message("assistant", final_content)
        self.sessions.save(session)

        return OutboundMessage(session_key=msg.session_key, content=final_content)

    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md, then trim session."""
        if not session.messages:
            return

        # use openviking tools to extract memory
        await hook_manager.execute_hooks(
            context=HookContext(
                event_type="message.compact",
                session_id=session.key.safe_name(),
                sandbox_key=self.sandbox_manager.to_sandbox_key(session.key),
            ),
            session=session,
        )

        if self.sandbox_manager:
            memory_workspace = self.sandbox_manager.get_workspace_path(session.key)
        else:
            memory_workspace = self.workspace

        memory = MemoryStore(memory_workspace)
        if archive_all:
            old_messages = session.messages
            keep_count = 0
        else:
            keep_count = min(10, max(2, self.memory_window // 2))
            old_messages = session.messages[:-keep_count]
        if not old_messages:
            return
        logger.info(
            f"Memory consolidation started: {len(session.messages)} messages, archiving {len(old_messages)}, keeping {keep_count}"
        )

        # Format messages for LLM (include tool names when available)
        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools_used = m.get("tools_used", [])
            if tools_used and isinstance(tools_used, list):
                tool_names = [
                    tc.get("tool_name", "unknown") for tc in tools_used if isinstance(tc, dict)
                ]
                tools_str = f" [tools: {', '.join(tool_names)}]" if tool_names else ""
            else:
                tools_str = ""
            lines.append(
                f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools_str}: {m['content']}"
            )
        conversation = "\n".join(lines)
        current_memory = memory.read_long_term()

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with exactly two keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by grep search later.

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged.

## Current Long-term Memory
{current_memory or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

        try:
            response = await self.provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": "You are a memory consolidation agent. Respond only with valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                model=self.model,
            )
            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(text)

            if entry := result.get("history_entry"):
                memory.append_history(entry)
            if update := result.get("memory_update"):
                if update != current_memory:
                    memory.write_long_term(update)

            session.messages = session.messages[-keep_count:] if keep_count else []
            self.sessions.save(session)
            logger.info(
                f"Memory consolidation done, session trimmed to {len(session.messages)} messages"
            )
        except Exception as e:
            logger.exception(f"Memory consolidation failed: {e}")

    async def process_direct(
        self,
        content: str,
        session_key: SessionKey = SessionKey(type="cli", channel_id="default", chat_id="direct"),
    ) -> str:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).

        Returns:
            The agent's response.
        """
        msg = InboundMessage(session_key=session_key, sender_id="user", content=content)

        response = await self._process_message(msg)
        return response.content if response else ""
