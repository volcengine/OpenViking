"""Small chat facade and task-scoped LoopX tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from vikingbot.agent.tools.base import MultimodalToolResult, Tool, ToolContext
from vikingbot.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from vikingbot.longtask.runner import LongTaskService, Turn


class StartLongTaskTool(Tool):
    name = "start_long_task"
    description = (
        "Start an explicitly requested long task in the background and immediately return its ID. "
        "Ordinary chat remains available. Include the complete objective, scope and acceptance "
        "criteria. Generate a request_id and reuse it if retrying the same submission."
    )
    parameters = {
        "type": "object",
        "properties": {
            "objective": {"type": "string", "minLength": 1, "maxLength": 4000},
            "request_id": {"type": "string", "minLength": 1, "maxLength": 128},
        },
        "required": ["objective", "request_id"],
        "additionalProperties": False,
    }

    def __init__(self, service: LongTaskService):
        self.service = service

    async def execute(self, tool_context: ToolContext, objective: str, request_id: str) -> str:
        return json.dumps(
            await self.service.create(tool_context, objective, request_id), ensure_ascii=False
        )


class LongTaskTool(Tool):
    name = "long_task"
    description = (
        "Inspect or control a long task belonging to this user and conversation. "
        "Pause/cancel/restart only on user request. Resume does not override unknown external "
        "effects or execution budgets. Status includes the latest durable LoopX decision."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "action": {"type": "string", "enum": ["status", "pause", "resume", "cancel"]},
            "message": {
                "type": "string",
                "maxLength": 4000,
                "description": "User's answer or additional instructions when resuming",
            },
        },
        "required": ["task_id", "action"],
        "additionalProperties": False,
    }

    def __init__(self, service: LongTaskService):
        self.service = service

    async def execute(
        self, tool_context: ToolContext, task_id: str, action: str, message: str = ""
    ) -> str:
        return json.dumps(
            await self.service.control(tool_context, task_id, action, message), ensure_ascii=False
        )


class LoopXReferenceTool(Tool):
    name = "loopx_reference"
    description = (
        "Read the complete matching-release LoopX workflow instructions before using that workflow."
    )
    parameters = {
        "type": "object",
        "properties": {"skill_id": {"type": "string"}},
        "required": ["skill_id"],
        "additionalProperties": False,
    }

    def __init__(self, workflows: dict[str, str]):
        self.workflows = workflows

    async def execute(self, tool_context: ToolContext, skill_id: str) -> str:
        if skill_id not in self.workflows:
            raise ValueError("Unknown LoopX workflow")
        return self.workflows[skill_id]


class SubmitLongTaskTurnTool(Tool):
    name = "submit_long_task_turn"
    description = (
        "End this bounded work round. Cite an operation ID from an actual successful tool result "
        "that verifies the work. The host writes LoopX state and checks it after the round. "
        "Use goal_complete only after checking every original acceptance criterion; otherwise "
        "provide next_todo. Use blocked_reason when user input or authorization is needed. "
        "Call this tool alone, not with other tools."
    )
    parameters = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "minLength": 1, "maxLength": 240},
            "evidence": {"type": "string", "minLength": 1, "maxLength": 420},
            "validation_operation_id": {"type": "string"},
            "goal_complete": {"type": "boolean"},
            "next_todo": {"type": "string", "maxLength": 1000},
            "blocked_reason": {"type": "string", "maxLength": 1000},
        },
        "required": ["summary", "evidence", "goal_complete"],
        "additionalProperties": False,
    }

    def __init__(self, turn: Turn):
        self.turn = turn

    async def execute(self, tool_context: ToolContext, **proposal: Any) -> str:
        if proposal["goal_complete"] and (
            proposal.get("next_todo") or proposal.get("blocked_reason")
        ):
            raise ValueError("Completed goals cannot have a successor or blocker")
        if not proposal.get("blocked_reason"):
            receipt = self.turn.successful_tools.get(proposal.get("validation_operation_id"))
            if receipt is None:
                raise ValueError("Cite a successful tool operation from this round as validation")
            if not proposal["goal_complete"] and not proposal.get("next_todo"):
                raise ValueError("Provide next_todo or a concrete blocked_reason")
        self.turn.proposal = proposal
        return "Round proposal recorded. The host will verify LoopX writeback before reporting completion."


class JournaledToolRegistry(ToolRegistry):
    """Serial host authorization and durable intent before every tool call."""

    def __init__(self, config: Any, turn: Turn):
        super().__init__(config=config)
        self.turn = turn

    async def execute_detailed(self, name: str, params: dict[str, Any], **kwargs: Any):
        from vikingbot.agent.tools.registry import ToolExecutionResult

        self.turn.service.store.assert_authorized(self.turn.task_id)
        if self.turn.proposal is not None:
            raise RuntimeError("This long-task round already submitted its result")
        # Reserve one call for durable submission instead of starting work that
        # cannot be followed by a checkpoint within the configured tool budget.
        limit = self.config.longtask.tools_per_round
        if self.turn.tool_count >= limit or (
            name != "submit_long_task_turn" and self.turn.tool_count >= limit - 1
        ):
            return ToolExecutionResult(
                result="Error: Work-tool budget reached. Submit the current round now.",
                effective_params=dict(params),
                success=False,
            )
        self.turn.tool_count += 1
        operation_id = self.turn.service.store.begin_operation(
            self.turn.task_id,
            self.turn.turn_id,
            "tool",
            {"name": name, "arguments": params},
        )
        result = await super().execute_detailed(name, params, **kwargs)
        text = str(result.result)
        succeeded = result.success
        non_mutating = {
            "read_file",
            "list_dir",
            "web_search",
            "web_fetch",
            "loopx_reference",
            "submit_long_task_turn",
            "openviking_list",
            "openviking_search",
            "openviking_grep",
            "openviking_glob",
            "openviking_multi_read",
        }
        if not succeeded and result.execution_started and name not in non_mutating:
            # A failed command can have partially succeeded outside the Bot.
            # Leave the intent unresolved rather than enabling automatic retries.
            raise RuntimeError(
                f"Tool {name} failed; inspect operation {operation_id} before resuming"
            )
        self.turn.service.store.end_operation(operation_id, {"name": name, "result": text})
        if name not in {"submit_long_task_turn", "loopx_reference"} and succeeded:
            self.turn.successful_tools[operation_id] = {"name": name, "result": text}
        # Operation IDs are evidence references, not proof of business acceptance.
        decorated = f"{'Error: ' if not succeeded else ''}operation_id={operation_id}\n{text}"
        if isinstance(result.result, MultimodalToolResult):
            decorated = MultimodalToolResult(
                text=decorated,
                content=[
                    {"type": "text", "text": f"operation_id={operation_id}"},
                    *result.result.content,
                ],
            )
        return ToolExecutionResult(
            result=decorated,
            effective_params=result.effective_params,
            skill_uris=result.skill_uris,
            success=succeeded,
            execution_started=result.execution_started,
        )

    async def close(self) -> None:
        from vikingbot.agent.tools.ov_file import OVFileTool

        for tool in self._tools.values():
            if isinstance(tool, OVFileTool):
                await tool.close()
