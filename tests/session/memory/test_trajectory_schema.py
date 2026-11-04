# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Trajectory prompt contract and Markdown storage compatibility (no model calls)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.schema_model_generator import SchemaModelGenerator
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.train.components.trajectory_analyzer import TrajectoryRolloutAnalyzer
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def trajectory_schema(monkeypatch):
    config = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    registry = MemoryTypeRegistry(load_schemas=False)
    template = (
        Path(__file__).resolve().parents[3]
        / "openviking/prompts/templates/memory/trajectories.yaml"
    )
    registry.load_from_yaml(str(template))
    return registry.get("trajectories")


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_trajectory_schema_exposes_factual_full_task_contract(trajectory_schema, language):
    assert "A factual execution record of one complete task." in trajectory_schema.description
    generator = SchemaModelGenerator([trajectory_schema], template_context={"language": language})
    model = generator.create_flat_data_model(trajectory_schema)
    content = model.model_fields["content"].description
    assert f"written in {language}" in content
    assert "{{ language }}" not in content
    for section in (
        "## Task and Constraints",
        "## Initial State and Policy",
        "## Execution Chain",
        "## Actual Result",
        "## Post-task Evaluation",
    ):
        assert section in content
    for label in (
        "Before:",
        "Basis:",
        "Assistant information:",
        "Deviation:",
        "Action:",
        "Observation:",
        "State change:",
    ):
        assert label in content
    initial_state = content.split("## Initial State and Policy\n", 1)[1].split(
        "## Execution Chain\n", 1
    )[0]
    assert [line for line in initial_state.splitlines() if line.startswith("### ")] == [
        "### Current policy",
        "### Initially known",
        "### Initially unknown",
    ]
    assert "#### Policy 1\n- Operation and target:" in initial_state
    for label in ("Applies when", "Rules", "Exceptions and limits"):
        assert f"- {label}:\n  > <policy excerpt" in initial_state
    policy = initial_state.split("### Current policy\n", 1)[1].split("### Initially known\n", 1)[0]
    execution = content.split("## Execution Chain\n", 1)[1].split("## Actual Result\n", 1)[0]
    basis = execution.split("- Basis:", 1)[1].split("- Assistant information:", 1)[0]
    assert "explicitly supplied policy and user requirements or confirmations only" in basis
    assert "Do not include retrieved Memory or assistant claims" in basis
    assistant_information = execution.split("- Assistant information:", 1)[1].split(
        "- Deviation:", 1
    )[0]
    assert (
        "tool results, retrieved Memory with its URI, and assistant claims" in assistant_information
    )
    assert "even if recorded earlier" in assistant_information
    assert "For each step, include both Basis and Assistant information" in execution
    assert 'Write "Not provided" when either has no relevant information' in execution
    assert 'Classify by source, not by wording such as "per policy"' in execution
    deviation = execution.split("- Deviation:", 1)[1].split("- Action:", 1)[0]
    assert "compare the two under the same operation and conditions" in deviation
    assert "contradictions or restrictions unsupported by Basis" in deviation
    result = content.split("## Actual Result\n", 1)[1].split("<!-- POST_TASK_EVALUATION -->", 1)[0]
    evaluation = content.split("## Post-task Evaluation\n", 1)[1]
    global_rules = content.split("Global rules:\n", 1)[1].split("Use the structure below", 1)[0]
    assert 'write "Not provided" under Current policy' in policy
    assert "Translate when needed without changing meaning" in policy
    assert "do not summarize or add interpretations" in policy
    assert "verbatim" not in content
    assert "original language" not in content
    assert "Keep each excerpt under the operation and target it originally governs" in policy
    assert "one complete dependent task chain in one record" in global_rules
    assert "Split only independent tasks" in global_rules
    assert "must not be backfilled into the initial state" in initial_state
    assert "Preserve the key non-secret concrete identifiers" in global_rules
    assert "source/message locator sections" in global_rules
    for section, instruction in (
        (policy, "evaluator reference answers are not policy sources"),
        (execution, "Do not rewrite a failed route as a successful procedure"),
        (result, "State unknowns when evidence is missing"),
        (evaluation, "never use it as execution-time knowledge"),
    ):
        assert instruction in section
        assert content.count(instruction) == 1
        assert instruction not in global_rules
    assert content.count("<!-- POST_TASK_EVALUATION -->") == 1
    assert "Omit both the marker and this section" in evaluation
    assert "never infer hidden reasoning" in execution.lower()
    assert "do not copy extraction instructions into the output" in content
    for obsolete in ("Operation Intent", "Negative Applicability", "Write Field Provenance"):
        assert obsolete not in content
    anchor = model.model_fields["retrieval_anchor"].description
    assert "Generalize instance-local identifiers" in anchor
    assert "Negative Applicability" not in anchor


def test_trajectory_schema_preserves_storage_fields_and_add_only(trajectory_schema):
    assert [field.name for field in trajectory_schema.fields] == [
        "trajectory_name",
        "outcome",
        "related_case_ids",
        "task_query",
        "retrieval_anchor",
        "content",
    ]
    assert trajectory_schema.directory == "viking://user/{{ user_space }}/memories/trajectories"
    assert trajectory_schema.filename_template == (
        "{{ trajectory_name|lower }}_{{ extract_context.get_session_timestamp() }}"
        "{{ extract_context.get_source_archive_suffix() }}.md"
    )
    assert trajectory_schema.operation_mode == "add_only"
    assert trajectory_schema.stage == "agent"
    generator = SchemaModelGenerator([trajectory_schema])
    operations_model = generator.create_structured_operations_model()
    assert "delete_ids" not in operations_model.model_fields


def test_experience_schema_preserves_dependencies_from_complete_trajectory():
    registry = MemoryTypeRegistry(load_schemas=False)
    template = (
        Path(__file__).resolve().parents[3] / "openviking/prompts/templates/memory/experiences.yaml"
    )
    registry.load_from_yaml(str(template))
    schema = registry.get("experiences")
    content = next(field.description for field in schema.fields if field.name == "content")

    assert "## Situation" in content
    assert "## Procedure" in content
    assert "## Verification" in content
    assert "## Anti-pattern" in content
    assert "## Approach" not in content
    assert "## Reflect" not in content
    assert "explicit user confirmation" in content
    assert "Preserve dependent steps and their order" in content
    assert "Split independent problems or methods" in content
    assert "Do not invent a successful route" in content
    assert "direct tool invocations only" not in content
    assert "STOP and split" not in content


_FACTUAL_BODY = """# 修改订单中指定乘客的姓氏
## 任务与约束
- 完整任务：将订单 3RK2T9 第二位乘客 Mei Lee 改为 Mei Garcia，保留其他信息。
## 起始状态与政策
### 当前政策
#### 政策 1
- 操作与对象：修改订单乘客信息
- 规则：
  > 写入前必须取得明确确认，人数不得改变。
### 起初已知
- 需要修改一位乘客姓名。
### 起初未知
- 订单号和新姓名。
## 执行链路
### 步骤 1：读取订单
- 执行前：用户已提供订单号 3RK2T9。
- 依据：用户提供的订单号。
- 动作：get_reservation_details({"reservation_id":"3RK2T9"})。
- 观察：第二位为 Mei Lee，出生日期 1989-12-13，订单共有 2 人。
- 状态变化：取得原始乘客列表。
### 步骤 2：取得确认
- 执行前：用户已指定第二位姓名改为 Mei Garcia，尚未授权写入。
- 依据：原始列表、用户要求及确认政策。
- 动作：复述修改范围，请用户确认。
- 观察：用户回复 Yes。
- 状态变化：未授权 → 已明确同意。
### 步骤 3：更新乘客
- 执行前：修改已获确认。
- 依据：步骤 1 的完整列表与步骤 2 的确认。
- 动作：update_reservation_passengers，保留完整两人列表，仅修改第二位 last_name=Garcia。
- 观察：工具返回第二位 Mei Garcia，dob=1989-12-13。
- 状态变化：Lee → Garcia；人数 2 → 2。
## 实际结果
- 已完成：第二位乘客改名，其他乘客和出生日期未改；未完成事项：无。
<!-- POST_TASK_EVALUATION -->
## 事后评测反馈
- score=1.0，db_match=true；属于任务结束后反馈。
"""


@pytest.mark.parametrize("body", [_FACTUAL_BODY, "# legacy\n- Procedure: verify then write"])
@pytest.mark.asyncio
async def test_new_and_legacy_trajectory_bodies_round_trip_and_load(trajectory_schema, body):
    fields = {
        "trajectory_name": "修改订单中指定乘客的姓氏",
        "outcome": "success",
        "related_case_ids": ["case-40"],
        "task_query": "将订单 3RK2T9 的第二位乘客 Mei Lee 改为 Mei Garcia。",
        "retrieval_anchor": "航空订单指定乘客改名，明确确认后保留完整乘客列表更新。",
        "content": body,
    }
    model = SchemaModelGenerator([trajectory_schema]).create_flat_data_model(trajectory_schema)
    extracted = model.model_validate({"page_id": 100, **fields})
    assert extracted.content == body
    uri = "viking://user/u/memories/trajectories/passenger_update_20261022.md"
    raw = MemoryFileUtils.write(
        MemoryFile(
            uri=uri,
            memory_type="trajectories",
            content=body,
            extra_fields={key: value for key, value in fields.items() if key != "content"},
        )
    )
    assert raw.count("<!-- MEMORY_FIELDS") == 1
    restored = MemoryFileUtils.read(raw, uri=uri)
    assert restored.content == body.strip()
    assert restored.extra_fields["task_query"] == fields["task_query"]
    assert restored.extra_fields["related_case_ids"] == ["case-40"]

    fs = SimpleNamespace(read_file=AsyncMock(return_value=raw))
    ctx = RequestContext(user=UserIdentifier("default", "u"), role=Role.USER)
    trajectories = await TrajectoryRolloutAnalyzer(viking_fs=fs)._read_trajectories([uri], ctx=ctx)
    assert len(trajectories) == 1
    assert trajectories[0].content == body.strip()
    assert trajectories[0].retrieval_anchor == fields["retrieval_anchor"]
    assert trajectories[0].outcome == "success"
