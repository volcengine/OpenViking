"""Tests for TaskPlanner — Plan-and-Execute layer."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

_spec = importlib.util.spec_from_file_location(
    "vikingbot.agent.planner",
    Path(__file__).resolve().parent.parent / "agent" / "planner.py",
)
_planner = importlib.util.module_from_spec(_spec)
import sys
sys.modules["vikingbot.agent.planner"] = _planner
_spec.loader.exec_module(_planner)
TaskPlanner = _planner.TaskPlanner
Plan = _planner.Plan
SubTask = _planner.SubTask


def _build_response(content: str) -> MagicMock:
    resp = MagicMock(spec=LLMResponse)
    resp.content = content
    return resp


# ============================================================
# SubTask
# ============================================================

class TestSubTask:
    def test_create_subtask(self):
        st = SubTask(id="step_1", goal="read a file")
        assert st.id == "step_1"
        assert st.goal == "read a file"
        assert st.depends_on == []
        assert st.status == "pending"
        assert st.result is None

    def test_subtask_round_trip(self):
        st = SubTask(id="s2", goal="search web", depends_on=["s1"], status="completed", result="done")
        data = st.to_dict()
        assert data["id"] == "s2"
        assert data["status"] == "completed"

        restored = SubTask.from_dict(data)
        assert restored.id == "s2"
        assert restored.goal == "search web"
        assert restored.depends_on == ["s1"]
        assert restored.status == "completed"
        assert restored.result == "done"


# ============================================================
# Plan
# ============================================================

class TestPlan:
    def _make_plan(self, *statuses: str) -> Plan:
        sts = [SubTask(id=f"step_{i}", goal=f"task {i}", status=s) for i, s in enumerate(statuses)]
        return Plan(plan_id="p1", reasoning="test", subtasks=sts)

    def test_get_next_returns_first_pending(self):
        plan = self._make_plan("completed", "pending", "pending")
        assert plan.get_next() is not None
        assert plan.get_next().id == "step_1"

    def test_get_next_returns_none_when_all_done(self):
        plan = self._make_plan("completed", "completed")
        assert plan.get_next() is None

    def test_all_completed(self):
        assert self._make_plan("completed", "completed").all_completed is True
        assert self._make_plan("completed", "pending").all_completed is False

    def test_pending_subtasks(self):
        pending = self._make_plan("completed", "failed", "pending").pending_subtasks
        assert len(pending) == 2

    def test_plan_round_trip(self):
        plan = self._make_plan("pending", "completed")
        plan.subtasks[1].result = "all good"
        data = plan.to_dict()
        restored = Plan.from_dict(data)
        assert restored.plan_id == "p1"
        assert len(restored.subtasks) == 2
        assert restored.subtasks[0].status == "pending"
        assert restored.subtasks[1].result == "all good"


# ============================================================
# TaskPlanner — parse / inject / mark / persist
# ============================================================

@pytest.fixture
def planner() -> TaskPlanner:
    provider = MagicMock()
    provider.chat = AsyncMock()
    with tempfile.TemporaryDirectory() as tmp:
        yield TaskPlanner(provider=provider, workspace=Path(tmp), model="test-model")


class TestParsePlanResponse:
    def test_plain_json(self, planner):
        result = planner._parse_plan_response('{"a": 1}')
        assert result == {"a": 1}

    def test_markdown_fence(self, planner):
        result = planner._parse_plan_response('```json\n{"b": 2}\n```')
        assert result == {"b": 2}

    def test_no_lang_fence(self, planner):
        result = planner._parse_plan_response('```\n{"c": 3}\n```')
        assert result == {"c": 3}

    def test_empty_raises(self, planner):
        with pytest.raises(ValueError):
            planner._parse_plan_response("")

    def test_none_raises(self, planner):
        with pytest.raises(ValueError):
            planner._parse_plan_response(None)


class TestInjectSubtaskGoal:
    def test_injects_goal_before_current_messages(self, planner):
        messages = [{"role": "user", "content": "hello"}]
        sub = SubTask(id="s1", goal="do something")
        result = planner.inject_subtask_goal(messages, sub)
        assert len(result) == 2
        assert "Current Sub-task" in result[1]["content"]
        assert "do something" in result[1]["content"]

    def test_shows_progress_when_plan_provided(self, planner):
        messages = [{"role": "user", "content": "hi"}]
        sub = SubTask(id="s2", goal="step two")
        plan = Plan(
            plan_id="p1", reasoning="",
            subtasks=[
                SubTask(id="s1", goal="step one", status="completed"),
                sub,
            ],
        )
        result = planner.inject_subtask_goal(messages, sub, plan)
        assert "[2/2]" in result[1]["content"]


class TestMarkCompleted:
    def test_mark_success(self, planner):
        sub = SubTask(id="s1", goal="task")
        planner.mark_completed(sub, "result ok", success=True)
        assert sub.status == "completed"
        assert sub.result == "result ok"

    def test_mark_failure(self, planner):
        sub = SubTask(id="s1", goal="task")
        planner.mark_completed(sub, "error happened", success=False)
        assert sub.status == "failed"
        assert sub.result == "error happened"

    def test_result_truncated(self, planner):
        sub = SubTask(id="s1", goal="task")
        long_result = "x" * 600
        planner.mark_completed(sub, long_result)
        assert len(sub.result) == 500


class TestShouldReplan:
    @pytest.mark.parametrize("result", [
        "Error: something broke",
        "Failed to connect",
        "Unable to process request",
        "cannot complete the task",
        "not available right now",
        "permission denied for resource",
    ])
    def test_detects_failure_keywords(self, planner, result):
        sub = SubTask(id="s1", goal="task")
        assert planner.should_replan(sub, result) is True

    @pytest.mark.parametrize("result", [
        "Task completed successfully",
        "Here is the result",
        "",
    ])
    def test_no_false_positive(self, planner, result):
        sub = SubTask(id="s1", goal="task")
        assert planner.should_replan(sub, result) is False

    def test_none_result(self, planner):
        sub = SubTask(id="s1", goal="task")
        assert planner.should_replan(sub, None) is False


class TestBuildSummary:
    def test_single_result(self, planner):
        sub = SubTask(id="s1", goal="find answer", status="completed", result="42")
        plan = Plan(plan_id="p1", reasoning="", subtasks=[sub])
        text = planner.build_summary(plan, [(sub, "42")])
        assert "Plan Execution Summary" in text
        assert "find answer" in text

    def test_mixed_status(self, planner):
        s1 = SubTask(id="s1", goal="ok task", status="completed", result="yes")
        s2 = SubTask(id="s2", goal="bad task", status="failed", result="nope")
        plan = Plan(plan_id="p1", reasoning="", subtasks=[s1, s2])
        text = planner.build_summary(plan, [(s1, "yes"), (s2, "nope")])
        assert "✓" in text
        assert "✗" in text

    def test_empty(self, planner):
        plan = Plan(plan_id="p1", reasoning="", subtasks=[])
        text = planner.build_summary(plan, [])
        assert text == ""


class TestPersistLoadClear:
    def test_round_trip(self, planner):
        session_key = MagicMock()
        session_key.safe_name.return_value = "test_session"

        plan = Plan(
            plan_id="p99", reasoning="test plan",
            subtasks=[
                SubTask(id="s1", goal="first", status="completed", result="ok"),
                SubTask(id="s2", goal="second", status="pending"),
            ],
        )

        planner.persist_plan(plan, session_key)

        loaded = planner.load_plan(session_key)
        assert loaded is not None
        assert loaded.plan_id == "p99"
        assert len(loaded.subtasks) == 2
        assert loaded.subtasks[0].status == "completed"
        assert loaded.subtasks[0].result == "ok"

        planner.clear_plan(session_key)
        assert planner.load_plan(session_key) is None

    def test_load_nonexistent(self, planner):
        session_key = MagicMock()
        session_key.safe_name.return_value = "nonexistent"
        assert planner.load_plan(session_key) is None

    def test_clear_nonexistent_no_error(self, planner):
        session_key = MagicMock()
        session_key.safe_name.return_value = "gone"
        planner.clear_plan(session_key)


# ============================================================
# generate_plan — with mock LLM
# ============================================================

class TestGeneratePlan:
    def test_single_step_plan(self, planner):
        planner.provider.chat.return_value = _build_response(
            json.dumps({
                "plan_id": "abc123",
                "reasoning": "simple task",
                "subtasks": [{"id": "s1", "goal": "just do it", "depends_on": [], "parallel_group": None}],
            })
        )

        plan = asyncio.run(planner.generate_plan([], "do one thing"))

        assert len(plan.subtasks) == 1
        assert plan.subtasks[0].goal == "just do it"

    def test_multi_step_plan(self, planner):
        planner.provider.chat.return_value = _build_response(
            json.dumps({
                "plan_id": "def456",
                "reasoning": "complex task needs decomposition",
                "subtasks": [
                    {"id": "s1", "goal": "research", "depends_on": [], "parallel_group": None},
                    {"id": "s2", "goal": "analyze", "depends_on": ["s1"], "parallel_group": None},
                    {"id": "s3", "goal": "report", "depends_on": ["s2"], "parallel_group": None},
                ],
            })
        )

        plan = asyncio.run(planner.generate_plan([], "complex query"))

        assert len(plan.subtasks) == 3
        assert plan.subtasks[1].depends_on == ["s1"]

    def test_fallback_on_exception(self, planner):
        planner.provider.chat.side_effect = Exception("LLM offline")

        plan = asyncio.run(planner.generate_plan([], "some query"))

        assert plan.plan_id == "fallback"
        assert len(plan.subtasks) == 1

    def test_fallback_on_invalid_json(self, planner):
        planner.provider.chat.return_value = _build_response("not json at all")

        plan = asyncio.run(planner.generate_plan([], "some query"))

        assert plan.plan_id == "fallback"
        assert len(plan.subtasks) == 1

    def test_disabled_planning_returns_single_step(self, planner):
        planner.enable_planning = False
        plan = asyncio.run(planner.generate_plan([], "any query"))
        assert plan.plan_id == "single_step"
        assert len(plan.subtasks) == 1


# ============================================================
# replan
# ============================================================

class TestReplan:
    def test_replan_preserves_completed(self, planner):
        planner.provider.chat.return_value = _build_response(
            json.dumps({
                "plan_id": "new_plan",
                "reasoning": "replanned",
                "subtasks": [
                    {"id": "s4", "goal": "alternative approach", "depends_on": [], "parallel_group": None},
                ],
            })
        )

        current = Plan(
            plan_id="old_plan", reasoning="",
            subtasks=[
                SubTask(id="s1", goal="first", status="completed", result="ok"),
                SubTask(id="s2", goal="second", status="failed", result="error: boom"),
                SubTask(id="s3", goal="third", status="pending"),
            ],
        )

        new_plan = asyncio.run(
            planner.replan([], current, current.subtasks[1])
        )

        assert new_plan.subtasks[0].status == "completed"
        assert len(new_plan.subtasks) == 2

    def test_replan_exception_returns_current(self, planner):
        planner.provider.chat.side_effect = Exception("replan failed")
        current = Plan(plan_id="p1", reasoning="", subtasks=[SubTask(id="s1", goal="t", status="failed", result="err")])

        result = asyncio.run(planner.replan([], current, current.subtasks[0]))
        assert result is current
