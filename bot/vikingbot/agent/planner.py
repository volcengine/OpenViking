"""任务规划器：代理循环的规划与执行层。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from vikingbot.config.schema import SessionKey
    from vikingbot.providers.base import LLMProvider

PLAN_SYSTEM_PROMPT = """You are a task planning assistant. Given a user request and conversation context, break the task down into a sequence of concrete sub-tasks.

Rules:
1. Each sub-task should be a self-contained, actionable step.
2. Sub-tasks should be ordered sequentially unless they are truly independent (then mark them as parallel).
3. Each sub-task must have a clear, specific goal that an LLM agent can execute.
4. Limit to at most 7 sub-tasks.
5. If the user request is simple enough to be a single step, return a plan with exactly 1 sub-task.

Respond with ONLY a valid JSON object with this exact structure:
{
    "plan_id": "a unique identifier string",
    "reasoning": "brief explanation of your decomposition strategy",
    "subtasks": [
        {
            "id": "unique_subtask_id",
            "goal": "clear description of what this subtask should accomplish",
            "depends_on": [],
            "parallel_group": null
        }
    ]
}

- "depends_on": list of subtask ids that must complete before this one. Empty list if no dependencies.
- "parallel_group": string group name for subtasks that can run in parallel, or null if sequential."""

REPLAN_SYSTEM_PROMPT = """You are a task planning assistant. Some sub-tasks have failed or are incomplete. Given the current plan state and failure information, revise the remaining plan.

Rules:
1. Keep completed sub-tasks as-is (do not modify completed ones).
2. For failed sub-tasks, analyze the failure reason and create alternative approaches.
3. Remove any sub-tasks that are no longer needed.
4. The new plan must cover the original user intent.

Respond with ONLY a valid JSON object with this exact structure:
{
    "plan_id": "a new plan id",
    "reasoning": "brief explanation of your replanning strategy",
    "subtasks": [
        ...remaining subtasks (completed ones preserved, failed ones replaced)
    ]
}"""

PLAN_STATE_DIR = ".plans"


@dataclass
class SubTask:
    """计划中的单个子任务。"""

    id: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str | None = None
    status: str = "pending"
    result: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """将子任务转换为字典。"""
        return {
            "id": self.id,
            "goal": self.goal,
            "depends_on": self.depends_on,
            "parallel_group": self.parallel_group,
            "status": self.status,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubTask:
        """从字典创建子任务。"""
        return cls(
            id=data.get("id", ""),
            goal=data.get("goal", ""),
            depends_on=data.get("depends_on", []),
            parallel_group=data.get("parallel_group"),
            status=data.get("status", "pending"),
            result=data.get("result"),
        )


@dataclass
class Plan:
    """由有序子任务组成的任务计划。"""

    plan_id: str
    reasoning: str
    subtasks: list[SubTask] = field(default_factory=list)
    current_index: int = 0

    @property
    def pending_subtasks(self) -> list[SubTask]:
        return [st for st in self.subtasks if st.status in ("pending", "failed")]

    @property
    def all_completed(self) -> bool:
        return all(st.status == "completed" for st in self.subtasks)

    def get_next(self) -> SubTask | None:
        for st in self.subtasks:
            if st.status in ("pending", "failed"):
                return st
        return None

    def to_dict(self) -> dict[str, Any]:
        """将计划转换为字典。"""
        return {
            "plan_id": self.plan_id,
            "reasoning": self.reasoning,
            "current_index": self.current_index,
            "subtasks": [st.to_dict() for st in self.subtasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        """从字典创建计划。"""
        subtasks = [SubTask.from_dict(st) for st in data.get("subtasks", [])]
        return cls(
            plan_id=data.get("plan_id", ""),
            reasoning=data.get("reasoning", ""),
            subtasks=subtasks,
            current_index=data.get("current_index", 0),
        )


class TaskPlanner:
    """生成和管理用于计划与执行代理的任务计划。"""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        model: str,
        temperature: float = 0.3,
        enable_planning: bool = True,
        max_plan_steps: int = 7,
    ):
        """初始化任务规划器。"""
        self.provider = provider
        self.workspace = workspace
        self.model = model
        self.temperature = temperature
        self.enable_planning = enable_planning
        self.max_plan_steps = max_plan_steps

    async def generate_plan(
        self,
        messages: list[dict[str, Any]],
        user_query: str,
        session_key: SessionKey | None = None,
    ) -> Plan:
        """为给定的用户查询生成任务计划。"""
        if not self.enable_planning:
            return Plan(
                plan_id="single_step",
                reasoning="Planning disabled, using single-step execution.",
                subtasks=[SubTask(id="step_1", goal=user_query)],
            )

        plan_messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"User request: {user_query}\n\n"
                    "Break this down into concrete sub-tasks. "
                    f"Return at most {self.max_plan_steps} sub-tasks."
                ),
            },
        ]

        try:
            response = await self.provider.chat(
                messages=plan_messages,
                model=self.model,
                temperature=self.temperature,
                session_id=session_key.safe_name() if session_key else None,
            )
            plan_data = self._parse_plan_response(response.content)
            return Plan(
                plan_id=plan_data.get("plan_id", uuid.uuid4().hex[:8]),
                reasoning=plan_data.get("reasoning", ""),
                subtasks=[
                    SubTask(
                        id=st.get("id", f"step_{i}"),
                        goal=st.get("goal", ""),
                        depends_on=st.get("depends_on", []),
                        parallel_group=st.get("parallel_group"),
                    )
                    for i, st in enumerate(plan_data.get("subtasks", []))
                ],
            )
        except Exception as e:
            logger.warning(f"Plan generation failed, falling back to single-step: {e}")
            return Plan(
                plan_id="fallback",
                reasoning=f"Plan generation failed: {e}",
                subtasks=[SubTask(id="step_1", goal=user_query)],
            )

    async def replan(
        self,
        messages: list[dict[str, Any]],
        current_plan: Plan,
        failed_subtask: SubTask,
        session_key: SessionKey | None = None,
    ) -> Plan:
        """Generate a revised plan when a sub-task fails."""
        failed_info = (
            f"Failed sub-task:\n"
            f"  ID: {failed_subtask.id}\n"
            f"  Goal: {failed_subtask.goal}\n"
            f"  Result: {failed_subtask.result}\n\n"
            f"Completed sub-tasks:\n"
        )
        for st in current_plan.subtasks:
            if st.status == "completed":
                failed_info += f"  - [{st.id}] {st.goal}: {st.result[:200] if st.result else 'done'}\n"

        replan_messages = [
            {"role": "system", "content": REPLAN_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Original plan state:\n{json.dumps(current_plan.to_dict(), indent=2)}\n\n"
                    f"{failed_info}\n\n"
                    "Revise the plan considering this failure. "
                    "Keep completed subtasks unchanged. "
                    f"Return at most {self.max_plan_steps} remaining sub-tasks."
                ),
            },
        ]

        try:
            response = await self.provider.chat(
                messages=replan_messages,
                model=self.model,
                temperature=self.temperature,
                session_id=session_key.safe_name() if session_key else None,
            )
            replan_data = self._parse_plan_response(response.content)

            completed_subtasks = [st for st in current_plan.subtasks if st.status == "completed"]
            new_subtasks = [
                SubTask(
                    id=st.get("id", f"step_{i}"),
                    goal=st.get("goal", ""),
                    depends_on=st.get("depends_on", []),
                    parallel_group=st.get("parallel_group"),
                )
                for i, st in enumerate(replan_data.get("subtasks", []))
            ]
            for merged_st in new_subtasks:
                for completed_st in completed_subtasks:
                    if merged_st.id == completed_st.id:
                        merged_st.status = "completed"
                        merged_st.result = completed_st.result

            return Plan(
                plan_id=replan_data.get("plan_id", uuid.uuid4().hex[:8]),
                reasoning=replan_data.get("reasoning", ""),
                subtasks=completed_subtasks + new_subtasks,
            )
        except Exception as e:
            logger.warning(f"Replan failed: {e}")
            return current_plan

    def _parse_plan_response(self, content: str | None) -> dict[str, Any]:
        """解析LLM返回的计划JSON响应。"""
        if not content:
            raise ValueError("Empty plan response")
        text = content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        if text.startswith("```json"):
            text = text[7:].strip()
        return json.loads(text)

    def inject_subtask_goal(
        self, messages: list[dict[str, Any]], subtask: SubTask, plan: Plan | None = None
    ) -> list[dict[str, Any]]:
        """将当前子任务目标注入到消息列表中。"""
        total = 0
        if plan:
            total = len(plan.subtasks)
        completed = sum(1 for st in (plan.subtasks if plan else []) if st.status == "completed")
        progress = f"[{completed + 1}/{total}]" if total > 0 else ""

        goal_message = {
            "role": "user",
            "content": (
                f"## Current Sub-task {progress}\n{subtask.goal}\n\n"
                "Focus on completing ONLY this sub-task. "
                "Do not work on other tasks unless explicitly requested."
            ),
        }
        return messages + [goal_message]

    def mark_completed(self, subtask: SubTask, result: str, success: bool = True) -> None:
        """将子任务标记为已完成或失败。"""
        subtask.status = "completed" if success else "failed"
        subtask.result = result[:500] if result else None
        if success:
            logger.info(f"[PLANNER]: Sub-task completed: [{subtask.id}] {subtask.goal}")
        else:
            logger.warning(f"[PLANNER]: Sub-task failed: [{subtask.id}] {subtask.goal}")

    def should_replan(self, subtask: SubTask, result: str) -> bool:
        """根据子任务结果判断是否需要重新规划。"""
        if not result:
            return False
        failure_keywords = [
            "error:",
            "failed to",
            "unable to",
            "cannot complete",
            "not available",
            "permission denied",
        ]
        result_lower = result.lower()
        return any(kw in result_lower for kw in failure_keywords)

    def persist_plan(self, plan: Plan, session_key: SessionKey) -> None:
        """将计划状态持久化到工作区文件系统，用于断点恢复。"""
        try:
            plans_dir = self.workspace / PLAN_STATE_DIR
            plans_dir.mkdir(parents=True, exist_ok=True)
            plan_file = plans_dir / f"{session_key.safe_name()}_plan.json"
            plan_file.write_text(json.dumps(plan.to_dict(), indent=2, ensure_ascii=False))
            logger.info(f"[PLANNER]: Plan persisted to {plan_file}")
        except Exception as e:
            logger.warning(f"[PLANNER]: Failed to persist plan: {e}")

    def load_plan(self, session_key: SessionKey) -> Plan | None:
        """加载持久化的计划用于断点恢复。"""
        try:
            plan_file = self.workspace / PLAN_STATE_DIR / f"{session_key.safe_name()}_plan.json"
            if not plan_file.exists():
                return None
            data = json.loads(plan_file.read_text())
            plan = Plan.from_dict(data)
            logger.info(f"[PLANNER]: Plan loaded from {plan_file}, {len(plan.subtasks)} subtasks")
            return plan
        except Exception as e:
            logger.warning(f"[PLANNER]: Failed to load plan: {e}")
            return None

    def clear_plan(self, session_key: SessionKey) -> None:
        """删除持久化的计划文件。"""
        try:
            plan_file = self.workspace / PLAN_STATE_DIR / f"{session_key.safe_name()}_plan.json"
            if plan_file.exists():
                plan_file.unlink()
        except Exception as e:
            logger.warning(f"[PLANNER]: Failed to clear plan: {e}")

    def build_summary(
        self, plan: Plan, subtask_results: list[tuple[SubTask, str]]
    ) -> str:
        """为最终响应构建所有已完成子任务的摘要。"""
        if not subtask_results:
            return ""
        lines = ["## Plan Execution Summary"]
        for st, result in subtask_results:
            status_icon = "✓" if st.status == "completed" else "✗"
            lines.append(f"- {status_icon} **{st.goal}**: {result[:200] if result else 'done'}")
        lines.append("\nNow provide the final answer to the user based on all gathered results.")
        return "\n".join(lines)
