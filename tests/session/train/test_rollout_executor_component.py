# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import pytest

from openviking.session.train import (
    Case,
    ExecutionContext,
    Experience,
    ExperienceSet,
    Rubric,
    RubricCriterion,
    SingleTurnLLMRolloutExecutor,
    default_single_turn_prompt,
)


class FakeVLM:
    def __init__(self, response="assistant answer"):
        self.response = response
        self.calls = []

    async def get_completion_async(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _case() -> Case:
    return Case(
        name="case-1",
        task_signature="booking_duplicate",
        input={"user_request": "cancel duplicate booking"},
        rubric=Rubric(
            name="booking_rubric",
            description="Cancel only the verified duplicate booking.",
            criteria=[
                RubricCriterion(
                    name="verify_duplicate",
                    description="Verify duplicate status first.",
                    required=True,
                    weight=1.0,
                )
            ],
        ),
    )


def _policy_set() -> ExperienceSet:
    return ExperienceSet(
        root_uri="viking://user/u/memories/experiences",
        policies=[
            Experience(
                name="booking_policy",
                uri="viking://user/u/memories/experiences/booking_policy.md",
                version=2,
                status="production",
                content="Always verify duplicates before cancellation.",
            )
        ],
    )


@pytest.mark.asyncio
async def test_single_turn_llm_rollout_executor_produces_rollout_messages():
    vlm = FakeVLM()
    executor = SingleTurnLLMRolloutExecutor(vlm=vlm, thinking=False)
    context = ExecutionContext(policy_snapshot_id="snapshot-1")

    rollouts = await executor.execute([_case()], _policy_set(), context)

    assert len(rollouts) == 1
    rollout = rollouts[0]
    assert rollout.case.name == "case-1"
    assert rollout.policy_snapshot_id == "snapshot-1"
    assert [message.role for message in rollout.messages] == ["user", "assistant"]
    assert "Always verify duplicates" in rollout.messages[0].content
    assert "cancel duplicate booking" in rollout.messages[0].content
    assert rollout.messages[1].content == "assistant answer"
    assert vlm.calls[0]["thinking"] is False
    assert vlm.calls[0]["prompt"] == rollout.messages[0].content


@pytest.mark.asyncio
async def test_single_turn_llm_rollout_executor_accepts_custom_prompt_builder():
    vlm = FakeVLM(response=type("Resp", (), {"content": "structured answer"})())

    def build_prompt(case, policy_set, context):
        return f"custom:{case.name}:{len(policy_set.policies)}:{context.policy_snapshot_id}"

    executor = SingleTurnLLMRolloutExecutor(vlm=vlm, prompt_builder=build_prompt)

    rollouts = await executor.execute(
        [_case()],
        _policy_set(),
        ExecutionContext(policy_snapshot_id="snapshot-2"),
    )

    assert rollouts[0].messages[0].content == "custom:case-1:1:snapshot-2"
    assert rollouts[0].messages[1].content == "structured answer"


def test_default_single_turn_prompt_contains_case_policy_and_rubric():
    prompt = default_single_turn_prompt(
        _case(),
        _policy_set(),
        ExecutionContext(policy_snapshot_id="snapshot-3"),
    )

    assert "Policy snapshot: snapshot-3" in prompt
    assert "booking_policy v2 [production]" in prompt
    assert "cancel duplicate booking" in prompt
    assert "verify_duplicate" in prompt


def test_tau2_rollout_messages_use_structured_tool_parts():
    from benchmark.tau2.train.rollout_executor import _build_rollout_messages
    from openviking.message import TextPart, ToolPart

    rollout_messages = _build_rollout_messages(
        system_prompt="policy",
        user_prompt="user request",
        tools_used=[
            {
                "tool_name": "get_user_details",
                "args": '{"user_id": "emma_kim_9957"}',
                "result": '{"membership": "gold"}',
            }
        ],
        final_content="done",
        evaluation_result=None,
        reward=1.0,
    )

    tool_call_message = rollout_messages[2]
    assert tool_call_message.role == "assistant"
    assert isinstance(tool_call_message.parts[0], ToolPart)
    assert tool_call_message.parts[0].tool_status == "running"
    assert tool_call_message.parts[0].tool_input == {"user_id": "emma_kim_9957"}
    assert not any(
        isinstance(part, TextPart) and "tool-call:" in part.text
        for message in rollout_messages
        for part in message.parts
    )

    tool_result_message = rollout_messages[3]
    assert tool_result_message.role == "user"
    assert isinstance(tool_result_message.parts[0], ToolPart)
    assert tool_result_message.parts[0].tool_status == "completed"
    assert tool_result_message.parts[0].tool_output == '{"membership": "gold"}'


def test_tau2_native_env_reward_handles_required_id_and_tool_call_ids():
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv

    env = Tau2BenchEnv("airline", "1")
    env.reset()
    env.tool_call("get_user_details", {"user_id": "raj_sanchez_7340"})
    env.tool_call("get_reservation_details", {"reservation_id": "Q69X3R"})

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.reward == 1.0


def test_tau2_native_env_records_communication_as_assistant_text():
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv

    env = Tau2BenchEnv("airline", "3")
    env.reset()
    env.tool_call("communicate_with_user", {"content": "You may bring 4 suitcases."})

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.communicate_checks[0].met is True


def test_tau2_final_answer_is_appended_for_native_evaluation():
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv
    from benchmark.tau2.train.rollout_executor import _append_final_answer_for_tau2_evaluation

    env = Tau2BenchEnv("airline", "3")
    env.reset()
    _append_final_answer_for_tau2_evaluation(env, "You may bring 4 suitcases.")

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.communicate_checks[0].met is True
