"""Spawn tool for creating background subagents."""

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from vikingbot.agent.subagent import SubagentManager
from vikingbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from vikingbot.agent.tools.base import ToolContext


class SpawnTool(Tool):
    """
    Tool to spawn a subagent for background task execution.

    The subagent runs asynchronously and announces its result back
    to the main agent when complete.
    """

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task for the subagent to complete",
                },
                "label": {
                    "type": "string",
                    "description": "Optional short label for the task (for display)",
                },
            },
            "required": ["task"],
        }

    async def execute(
        self, tool_context: "ToolContext", task: str, label: str | None = None, **kwargs: Any
    ) -> str:
        """Spawn a subagent to execute the given task."""
        return await self._manager.spawn(
            task=task,
            label=label,
            session_key=tool_context.session_key,
            channel_metadata=tool_context.channel_metadata,
            openviking_connection=tool_context.openviking_connection,
        )


class WaitSubagentsTool(Tool):
    """Collect compact results from the current task's children without importing their histories."""

    def __init__(
        self,
        manager: SubagentManager,
        *,
        final_output_directory: str | None = None,
        collection_guard: Callable[[], str | None] | None = None,
    ):
        """Optionally disclose a Resource destination after the final child result is collected."""
        self._manager = manager
        self._final_output_directory = final_output_directory
        # A task-owned merge executor can reserve result collection while its plan runs.
        self._collection_guard = collection_guard
        # Retain failed child IDs after their result inventories are drained.
        self.failures: list[str] = []

    @property
    def name(self) -> str:
        return "wait_subagents"

    @property
    def description(self) -> str:
        return (
            "Collect child results, running/queued IDs and available capacity. "
            "Collect all children before submission."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "block": {
                    "type": "boolean",
                    "default": True,
                    "description": "Wait for new results; false returns the current status immediately.",
                },
                "wait_all": {
                    "type": "boolean",
                    "default": False,
                    "description": "With block=true, wait for all admitted children and return all their results together.",
                },
            },
            "additionalProperties": False,
        }

    async def execute(
        self, tool_context: "ToolContext", block: bool = True, wait_all: bool = False, **kwargs: Any
    ) -> str:
        """Return one completion or all admitted work, including failures, without model polling.

        wait_all requires blocking mode and accumulates inventories until both active
        and queued work finish. Cancellation propagates to the caller. Final output
        is announced only when a nonempty collection drains all work.
        """
        if wait_all and not block:
            return "Error: wait_all=true requires block=true."
        if self._collection_guard is not None:
            error = self._collection_guard()
            if error:
                return error
        report = await self._manager.wait(block=block)
        results = list(report["results"])
        while wait_all and (report["running"] or report["queued"]):
            report = await self._manager.wait(block=True)
            results.extend(report["results"])
        report["results"] = results
        self.failures.extend(
            f"Child {result['task_id']}: {result.get('error') or result['status']}"
            for result in results
            if result.get("status") in {"failed", "cancelled"}
        )
        if (
            self._final_output_directory is not None
            and report["results"]
            and not report["running"]
            and not report["queued"]
        ):
            report["final_output_directory"] = self._final_output_directory
            report["next_step"] = (
                "Save source draft IDs in named topic groups with merge_compile_drafts. "
                "Repair missing or conflicting assignments, then call run=true to execute bounded batches. "
                "After complete merge coverage, add navigation and submit."
            )
        return json.dumps(report, ensure_ascii=False)
