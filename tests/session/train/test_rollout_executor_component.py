# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import time

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


def test_dataset_service_policy_set_from_dict_preserves_policies():
    from openviking.session.train.components.dataset_service import policy_set_from_dict

    policy_set = policy_set_from_dict(
        {
            "root_uri": "viking://user/u/memories/experiences",
            "policies": [
                {
                    "name": "booking_policy",
                    "uri": "viking://user/u/memories/experiences/booking_policy.md",
                    "version": 2,
                    "status": "production",
                    "content": "Always verify duplicates before cancellation.",
                    "metadata": {"domain": "booking"},
                }
            ],
            "metadata": {"snapshot": "remote"},
        }
    )

    assert policy_set.root_uri == "viking://user/u/memories/experiences"
    assert policy_set.metadata == {"snapshot": "remote"}
    assert len(policy_set.policies) == 1
    policy = policy_set.policies[0]
    assert policy.name == "booking_policy"
    assert policy.uri == "viking://user/u/memories/experiences/booking_policy.md"
    assert policy.version == 2
    assert policy.status == "production"
    assert policy.content == "Always verify duplicates before cancellation."
    assert policy.metadata == {"domain": "booking"}


def test_tau2_rollout_messages_use_structured_tool_parts():
    from benchmark.tau2.train.rollout_executor import _build_rollout_messages
    from openviking.message import ControlPart, TextPart, ToolPart

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

    assert isinstance(rollout_messages[0].parts[0], ControlPart)
    assert rollout_messages[0].parts[0].control_type == "tau2_system_prompt"

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


def test_tau2_rollout_messages_omit_empty_final_after_done():
    from benchmark.tau2.train.rollout_executor import _build_rollout_messages

    rollout_messages = _build_rollout_messages(
        system_prompt="policy",
        user_prompt="user request",
        tools_used=[{"tool_name": "done", "args": "{}", "result": "Task Terminated"}],
        final_content=None,
        evaluation_result=None,
        reward=1.0,
    )

    assert "tau2-final" not in {message.id for message in rollout_messages}
    assert rollout_messages[-1].id == "tau2-reward"


def test_tau2_reward_info_is_json_safe_in_rollout_messages_and_evaluation():
    import json

    from tau2.data_model.simulation import RewardInfo, RewardType

    from benchmark.tau2.train.rollout_executor import _build_rollout_messages, _tau2_evaluation

    reward_info = RewardInfo(
        reward=1.0,
        reward_basis=[RewardType.DB],
        reward_breakdown={RewardType.DB: 1.0},
    )

    rollout_messages = _build_rollout_messages(
        system_prompt="policy",
        user_prompt="user request",
        tools_used=[],
        final_content="done",
        evaluation_result=reward_info,
        reward=1.0,
    )
    evaluation = _tau2_evaluation(reward=1.0, evaluation_result=reward_info)

    reward_message = rollout_messages[-1].content
    assert "'reward': 1.0" not in reward_message
    assert '"reward": 1.0' in reward_message
    assert '"reward_basis": ["DB"]' in evaluation.feedback[0]
    json.dumps(evaluation.metadata, sort_keys=True)


def test_tau2_native_env_reward_handles_required_id_and_tool_call_ids(monkeypatch):
    import benchmark.tau2.common.tau2_env.tau2_environment as tau2_environment
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv

    monkeypatch.setattr(tau2_environment, "AgentGymEnv", None)
    env = Tau2BenchEnv("airline", "1")
    env.reset()
    env.tool_call("get_user_details", {"user_id": "raj_sanchez_7340"})
    env.tool_call("get_reservation_details", {"reservation_id": "Q69X3R"})

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.reward == 1.0


def test_tau2_native_env_records_communication_as_assistant_text(monkeypatch):
    import benchmark.tau2.common.tau2_env.tau2_environment as tau2_environment
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv

    monkeypatch.setattr(tau2_environment, "AgentGymEnv", None)
    env = Tau2BenchEnv("airline", "3")
    env.reset()
    env.tool_call("communicate_with_user", {"content": "You may bring 4 suitcases."})

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.communicate_checks[0].met is True


def test_tau2_final_answer_is_appended_for_native_evaluation(monkeypatch):
    import benchmark.tau2.common.tau2_env.tau2_environment as tau2_environment
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv
    from benchmark.tau2.train.rollout_executor import _append_final_answer_for_tau2_evaluation

    monkeypatch.setattr(tau2_environment, "AgentGymEnv", None)
    env = Tau2BenchEnv("airline", "3")
    env.reset()
    _append_final_answer_for_tau2_evaluation(env, "You may bring 4 suitcases.")

    reward, evaluation = env._impl._get_reward()

    assert reward == 1.0
    assert evaluation.communicate_checks[0].met is True


def test_tau2_configure_tools_removes_only_openviking_tools():
    from benchmark.tau2.train.rollout_executor import _configure_tools

    class FakeTools:
        def __init__(self):
            self.tool_names = [
                "read_file",
                "openviking_search",
                "openviking_memory_commit",
                "web_search",
            ]
            self.unregistered = []
            self.registered = []

        def unregister(self, name):
            self.unregistered.append(name)
            self.tool_names.remove(name)

        def register(self, tool):
            self.registered.append(tool.name)

    class FakeAgent:
        def __init__(self):
            self.tools = FakeTools()

    class FakeProvider:
        def list_openai_tools(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": "get_user_details",
                        "description": "get user",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ]

        def call_tool(self, name, args):
            return "ok"

    agent = FakeAgent()

    _configure_tools(agent, FakeProvider(), keep_default_tools=True)

    assert agent.tools.unregistered == ["openviking_search", "openviking_memory_commit"]
    assert agent.tools.tool_names == ["read_file", "web_search"]
    assert agent.tools.registered == ["get_user_details"]


def test_tau2_rollout_backend_factory_defaults_to_native():
    from benchmark.tau2.train.rollout_executor import (
        NativeTau2RolloutExecutor,
        make_tau2_rollout_executor,
        normalize_tau2_rollout_backend,
    )

    executor = make_tau2_rollout_executor(
        options={"keep_default_tools": False, "max_iterations": 7},
        concurrency=3,
    )

    assert normalize_tau2_rollout_backend(None) == "native"
    assert isinstance(executor, NativeTau2RolloutExecutor)
    assert executor.concurrency == 3
    assert executor.memory_enabled is False
    assert executor.max_steps == 7
    assert executor.show_progress is False


def test_tau2_native_rollout_resolves_non_empty_llms(monkeypatch):
    from benchmark.tau2.train.rollout_executor_native import (
        NativeTau2RolloutExecutor,
        _resolve_llm_runtime_config,
    )

    monkeypatch.delenv("TAU2_AGENT_LLM", raising=False)
    monkeypatch.delenv("TAU2_USER_LLM", raising=False)

    agent_llm, agent_args, user_llm, user_args = _resolve_llm_runtime_config(
        NativeTau2RolloutExecutor(
            agent_llm_args={"temperature": 0.2},
            user_llm_args={"top_p": 0.9},
        )
    )

    assert agent_llm
    assert user_llm
    assert agent_args["temperature"] == 0.2
    assert user_args["temperature"] == 0.0
    assert user_args["top_p"] == 0.9


def test_tau2_native_rollout_uses_env_llm_when_options_omit_model(monkeypatch):
    from benchmark.tau2.train.rollout_executor_native import (
        NativeTau2RolloutExecutor,
        _resolve_llm_runtime_config,
    )

    monkeypatch.setenv("TAU2_AGENT_LLM", "openai/test-agent")
    monkeypatch.setenv("TAU2_USER_LLM", "openai/test-user")

    agent_llm, _agent_args, user_llm, _user_args = _resolve_llm_runtime_config(
        NativeTau2RolloutExecutor()
    )

    assert agent_llm == "openai/test-agent"
    assert user_llm == "openai/test-user"


def test_tau2_rollout_backend_factory_selects_vikingbot(monkeypatch):
    import benchmark.tau2.train.rollout_executor as module

    created = {}

    class FakeVikingBotExecutor:
        def __init__(self, **kwargs):
            created.update(kwargs)

    monkeypatch.setattr(module, "VikingBotTau2RolloutExecutor", FakeVikingBotExecutor)

    executor = module.make_tau2_rollout_executor(
        backend="vikingbot",
        options={
            "config_path": "/tmp/ov.conf",
            "max_iterations": 9,
        },
        concurrency=2,
        rollout_language="zh",
    )

    assert isinstance(executor, FakeVikingBotExecutor)
    assert created == {
        "config_path": "/tmp/ov.conf",
        "concurrency": 2,
        "keep_default_tools": True,
        "max_iterations": 9,
        "rollout_language": "zh",
    }


def test_tau2_service_rollout_backend_option_overrides_default(monkeypatch):
    import benchmark.tau2.train.service_app as service_app

    calls = []

    def fake_create_dataset_service_app(**kwargs):
        calls.append(kwargs)
        return kwargs

    class FakeExecutor:
        pass

    def fake_make_tau2_rollout_executor(**kwargs):
        calls.append({"factory": kwargs})
        return FakeExecutor()

    monkeypatch.setattr(service_app, "create_dataset_service_app", fake_create_dataset_service_app)
    monkeypatch.setattr(service_app, "make_tau2_rollout_executor", fake_make_tau2_rollout_executor)

    app = service_app.create_app(rollout_backend="native")
    executor = app["make_rollout_executor"]({"rollout_backend": "vikingbot", "max_iterations": 5})

    assert isinstance(executor, FakeExecutor)
    assert calls[-1]["factory"]["backend"] == "vikingbot"
    assert calls[-1]["factory"]["options"]["max_iterations"] == 5
    assert calls[-1]["factory"]["options"]["show_progress"] is False

    app["make_rollout_executor"]({"rollout_backend": "native", "show_progress": True})
    assert calls[-1]["factory"]["options"]["show_progress"] is True


@pytest.mark.asyncio
async def test_tau2_vikingbot_rollout_does_not_block_event_loop(monkeypatch):
    from benchmark.tau2.train.rollout_executor_vikingbot import VikingBotTau2RolloutExecutor

    class FakeVikingBotExecutor(VikingBotTau2RolloutExecutor):
        async def _execute_one_async(self, case, context):
            del context
            time.sleep(0.2)
            return case.name

    executor = FakeVikingBotExecutor()
    heartbeat = asyncio.create_task(asyncio.sleep(0.05))
    rollout_task = asyncio.create_task(
        executor._execute_one(
            _case(),
            ExecutionContext(policy_snapshot_id="snapshot", metadata={}),
        )
    )

    await asyncio.wait_for(heartbeat, timeout=0.15)
    assert not rollout_task.done()
    assert await rollout_task == "case-1"
