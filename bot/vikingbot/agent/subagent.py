"""Subagent manager for background task execution."""

import asyncio
import json
import time as _time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from vikingbot.agent.tools.registry import ToolRegistry
from vikingbot.bus.events import InboundMessage
from vikingbot.bus.queue import MessageBus
from vikingbot.config.schema import SessionKey
from vikingbot.providers.base import LLMProvider
from vikingbot.sandbox.manager import SandboxManager
from vikingbot.utils.helpers import ensure_non_empty_assistant_content

if TYPE_CHECKING:
    from vikingbot.config.schema import Config


class SubagentManager:
    """
    Manages background subagent execution.

    Subagents are lightweight agent instances that run in the background
    to handle specific tasks. They share the same LLM provider but have
    isolated context and a focused system prompt. A request-owned task_runner
    can supply its own execution loop and collect results with wait() instead
    of publishing chat notifications.
    """

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        config: "Config",
        model: str | None = None,
        temperature: float = 0.7,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.config = config
        self.model = model or provider.get_default_model()
        self.temperature = temperature
        self.sandbox_manager = sandbox_manager
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._max_concurrency = getattr(
            getattr(config, "agents", None), "subagent_max_concurrency", 8
        )
        self._worker_slots = asyncio.Semaphore(self._max_concurrency)
        self._active_tasks: set[str] = set()
        # A request-owned runner returns compact results to wait(), without chat notifications.
        self.task_runner: Callable[[str, str, SessionKey], Awaitable[dict[str, Any]]] | None = None
        self._completed_results: dict[str, dict[str, Any]] = {}
        self._closed = False

    async def spawn(
        self,
        task: str,
        session_key: SessionKey,
        label: str | None = None,
        channel_metadata: dict[str, Any] | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> str:
        """
        Spawn a subagent to execute a task in the background.

        Args:
            task: The task description for the subagent.
            session_key: Parent session used for workspace access and result routing.
            label: Optional human-readable label for the task.
            channel_metadata: Delivery metadata for chat notifications.
            openviking_connection: Request-scoped identity for child tools.

        Returns:
            A started or queued message with the task ID, or an error when spawning is closed
            or capacity is exhausted. Managed tasks queue up to twice the worker count,
            including uncollected results; chat tasks start immediately or return an error.
        """
        if self._closed:
            return "Error: Subagent spawning is closed for this task."
        if self.task_runner is not None:
            outstanding = sum(not task.done() for task in self._running_tasks.values())
            if outstanding + len(self._completed_results) >= 2 * self._max_concurrency:
                return "Error: Subagent queue is full. Call wait_subagents, then spawn the remaining assignments."
        elif self.get_running_count() >= self._max_concurrency:
            return f"Error: Subagent concurrency limit reached ({self._max_concurrency})"

        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")

        # Create background task
        run = (
            self._run_managed_subagent(task_id, task, display_label, session_key)
            if self.task_runner is not None
            else self._run_subagent(
                task_id,
                task,
                display_label,
                session_key,
                dict(channel_metadata or {}),
                openviking_connection,
            )
        )
        bg_task = asyncio.create_task(run)
        self._running_tasks[task_id] = bg_task

        # Cleanup when done
        bg_task.add_done_callback(lambda _: self._running_tasks.pop(task_id, None))

        logger.info(f"Spawned subagent [{task_id}]: {display_label}")
        if self.task_runner is not None:
            return f"Subagent accepted (id: {task_id})."
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_managed_subagent(
        self, task_id: str, task: str, label: str, session_key: SessionKey
    ) -> None:
        """Run a request-owned child and retain its outcome until the parent collects it."""
        result: dict[str, Any] = {"task_id": task_id, "label": label}
        try:
            async with self._worker_slots:
                self._active_tasks.add(task_id)
                assert self.task_runner is not None
                result.update(await self.task_runner(task_id, task, session_key))
                result["status"] = "completed"
        except asyncio.CancelledError:
            result["status"] = "cancelled"
            raise
        except Exception as exc:
            result.update(status="failed", error=str(exc)[:1500])
            logger.exception("Subagent [{}] failed", task_id)
        finally:
            self._active_tasks.discard(task_id)
            self._completed_results[task_id] = result

    async def wait(self, *, block: bool = True) -> dict[str, Any]:
        """Collect new outcomes, waiting for a child to finish unless block is false.

        Blocking waits return when results exist or no children remain, and propagate cancellation.
        Return active/queued IDs, worker limits and free slots after draining results.
        queue_capacity counts additional admissions; capacity is its compatibility alias.
        Completed results are delivered once; unfinished children keep running.
        """
        while block and not self._completed_results:
            running = [task for task in self._running_tasks.values() if not task.done()]
            if not running:
                break
            await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
        results = list(self._completed_results.values())
        self._completed_results.clear()
        pending = [key for key, task in self._running_tasks.items() if not task.done()]
        queue_capacity = max(0, 2 * self._max_concurrency - len(pending))
        return {
            "results": results,
            "running": [key for key in pending if key in self._active_tasks],
            "queued": [key for key in pending if key not in self._active_tasks],
            "max_concurrency": self._max_concurrency,
            "free_worker_slots": max(0, self._max_concurrency - len(self._active_tasks)),
            "queue_capacity": queue_capacity,
            "capacity": queue_capacity,
        }

    def begin_submission(self, finalize: bool = False) -> str | None:
        """Reject uncollected child work; seal spawning when validated output is finalized."""
        if any(not task.done() for task in self._running_tasks.values()) or self._completed_results:
            return "Error: Call wait_subagents to collect all child results and merge their drafts before submitting."
        if finalize:
            self._closed = True
        return None

    async def cancel_all(self) -> None:
        """Stop accepting children and await cancellation before their workspace is removed."""
        self._closed = True
        tasks = list(self._running_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        session_key: SessionKey,
        channel_metadata: dict[str, Any] | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info(f"Subagent [{task_id}] starting task: {label}")

        try:
            # Build subagent tools (no message tool, no spawn tool)
            from vikingbot.agent.tools import register_subagent_tools

            tools = ToolRegistry(config=self.config)
            register_subagent_tools(
                registry=tools,
                config=self.config,
            )

            # Search experience memory relevant to this subtask
            task_content = task
            try:
                from vikingbot.agent.memory import MemoryStore

                if not self.config.ov_server.is_available():
                    raise RuntimeError("OpenViking is unavailable")
                memory_store = MemoryStore(self.workspace, config=self.config)
                workspace_id = (
                    self.sandbox_manager.to_workspace_id(session_key)
                    if self.sandbox_manager
                    else "shared"
                )
                exp_memory = await memory_store.get_viking_experience_context(
                    query=task,
                    workspace_id=workspace_id,
                    openviking_connection=openviking_connection,
                )
                if exp_memory:
                    task_content = f"## Agent Experience (relevant to this task)\n{exp_memory}\n\n---\n\n{task}"
            except Exception as e:
                logger.warning(f"Subagent [{task_id}] failed to load experience memory: {e}")

            # Build messages with subagent-specific prompt
            prompt_workspace = await self._get_session_workspace(session_key)
            system_prompt = self._build_subagent_prompt(task, workspace=prompt_workspace)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task_content},
            ]

            # Run agent loop (limited iterations)
            max_iterations = 15
            iteration = 0
            final_result: str | None = None

            while iteration < max_iterations:
                iteration += 1

                response = await self.provider.chat(
                    messages=messages,
                    tools=tools.get_definitions(),
                    model=self.model,
                    temperature=self.temperature,
                )

                if response.has_tool_calls:
                    # Add assistant message with tool calls
                    tool_call_dicts = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                # Keep Unicode readable in model-facing tool history.
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in response.tool_calls
                    ]
                    messages.append(
                        {
                            "role": "assistant",
                            "content": ensure_non_empty_assistant_content(response.content),
                            "tool_calls": tool_call_dicts,
                        }
                    )

                    # Execute tools
                    for tool_call in response.tool_calls:
                        args_str = json.dumps(tool_call.arguments)
                        logger.debug(
                            f"Subagent [{task_id}] executing: {tool_call.name} with arguments: {args_str}"
                        )
                        result = await tools.execute(
                            tool_call.name,
                            tool_call.arguments,
                            session_key=session_key,
                            sandbox_manager=self.sandbox_manager,
                            openviking_connection=openviking_connection,
                        )
                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "name": tool_call.name,
                                "content": result,
                            }
                        )
                else:
                    final_result = response.content
                    break

            if final_result is None:
                final_result = "Task completed but no final response was generated."

            logger.info(f"Subagent [{task_id}] completed successfully")
            await self._announce_result(
                task_id,
                label,
                task,
                final_result,
                session_key,
                "ok",
                channel_metadata,
                openviking_connection,
            )

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            logger.exception(f"Subagent [{task_id}] failed: {e}")
            await self._announce_result(
                task_id,
                label,
                task,
                error_msg,
                session_key,
                "error",
                channel_metadata,
                openviking_connection,
            )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        session_key: SessionKey,
        status: str,
        channel_metadata: dict[str, Any] | None = None,
        openviking_connection: dict[str, Any] | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = f"""[Subagent '{label}' {status_text}]

Task: {task}

Result:
{result}

Summarize this naturally for the user. Keep it brief (1-2 sentences). Do not mention technical details like "subagent" or task IDs."""

        # Inject as system message to trigger main agent
        msg = InboundMessage(
            sender_id="subagent",
            session_key=session_key,
            content=announce_content,
            metadata=dict(channel_metadata or {}),
            openviking_connection=openviking_connection,
        )

        await self.bus.publish_inbound(msg)
        logger.debug(f"Subagent [{task_id}] announced result to {session_key}")

    async def _get_session_workspace(self, session_key: SessionKey) -> Path:
        """Return the workspace path used by tools for this subagent session."""
        if not self.sandbox_manager:
            return self.workspace

        await self.sandbox_manager.get_sandbox(session_key)
        return self.sandbox_manager.get_workspace_path(session_key)

    def _build_subagent_prompt(self, task: str, workspace: Path | None = None) -> str:
        """Build a focused system prompt for the subagent."""
        workspace = workspace or self.workspace
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        skills_context = self._build_subagent_skills_context(workspace)

        prompt = f"""# Subagent

## Current Time
{now} ({tz})

You are a subagent spawned by the main agent to complete a specific task.

## Rules
1. Stay focused - complete only the assigned task, nothing else
2. Your final response will be reported back to the main agent
3. Do not initiate conversations or take on side tasks
4. Be concise but informative in your findings

## What You Can Do
- Read and write files in the workspace
- Execute shell commands
- Search the web and fetch web pages
- Complete the task thoroughly

## What You Cannot Do
- Send messages directly to users (no message tool available)
- Spawn other subagents
- Access the main agent's conversation history

## Workspace
Your workspace is at: {workspace}
Skills are available at: {workspace}/skills/ (read SKILL.md files as needed)

When you have completed the task, provide a clear summary of your findings or actions."""
        if skills_context:
            prompt = f"{prompt}\n\n{skills_context}"
        return prompt

    def _build_subagent_skills_context(self, workspace: Path | None = None) -> str:
        """Build the same local skills context format used by the main agent."""
        try:
            from vikingbot.agent.skills import SkillsLoader

            workspace = workspace or self.workspace
            skills = SkillsLoader(workspace)
            parts: list[str] = []

            always_skills = skills.get_always_skills()
            if always_skills:
                always_content = skills.load_skills_for_context(always_skills)
                if always_content:
                    parts.append(f"# Active Skills\n\n{always_content}")

            skills_summary = skills.build_skills_summary()
            if skills_summary:
                required_skill_note = ""
                required_skill_candidates = [
                    "skills/experience_loader/SKILL.md",
                    "skills/task_case_experience/SKILL.md",
                ]
                for skill_path in required_skill_candidates:
                    if (workspace / skill_path).exists():
                        required_skill_note = (
                            "\nRequired skill: before taking any task action, you MUST read "
                            f"`{skill_path}` and apply its instructions.\n"
                        )
                        break
                parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.
{required_skill_note}
{skills_summary}""")

            return "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"Failed to build subagent skills context: {e}")
            return ""

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        if self.task_runner is not None:
            return len(self._active_tasks)
        return sum(not task.done() for task in self._running_tasks.values())
