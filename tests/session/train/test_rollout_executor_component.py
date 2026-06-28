# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

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


def test_tau2_rollout_messages_use_completed_structured_tool_parts():
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

    assert isinstance(rollout_messages[0].parts[0], TextPart)
    assert rollout_messages[0].parts[0].text.startswith("system:\npolicy")

    tool_message = rollout_messages[2]
    assert tool_message.role == "user"
    assert isinstance(tool_message.parts[0], ToolPart)
    assert tool_message.parts[0].tool_status == "completed"
    assert tool_message.parts[0].tool_input == {"user_id": "emma_kim_9957"}
    assert tool_message.parts[0].tool_output == '{"membership": "gold"}'
    assert not any(
        isinstance(part, TextPart) and "tool-call:" in part.text
        for message in rollout_messages
        for part in message.parts
    )
    assert not any(
        isinstance(part, ToolPart) and part.tool_status == "running"
        for message in rollout_messages
        for part in message.parts
    )


def test_tau2_communicate_with_user_renders_as_dialogue():
    from benchmark.tau2.train.rollout_executor import _build_rollout_messages
    from openviking.message import TextPart, ToolPart

    rollout_messages = _build_rollout_messages(
        system_prompt="policy",
        user_prompt="user request",
        tools_used=[
            {
                "tool_name": "communicate_with_user",
                "args": {"content": "Could you provide your user ID?"},
                "result": "Sure, it is emma_kim_9957.",
            }
        ],
        final_content=None,
        evaluation_result=None,
        reward=1.0,
    )

    assert rollout_messages[2].role == "assistant"
    assert isinstance(rollout_messages[2].parts[0], TextPart)
    assert rollout_messages[2].parts[0].text == "Could you provide your user ID?"
    assert rollout_messages[3].role == "user"
    assert isinstance(rollout_messages[3].parts[0], TextPart)
    assert rollout_messages[3].parts[0].text == "Sure, it is emma_kim_9957."
    assert not any(
        isinstance(part, ToolPart) and part.tool_name == "communicate_with_user"
        for message in rollout_messages
        for part in message.parts
    )


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


def test_tau2_litellm_generate_rate_limit_retry_patch(monkeypatch):
    import benchmark.tau2.common.tau2_env.tau2_environment as tau2_environment

    calls = {"count": 0}
    sleeps = []

    def fake_generate():
        calls["count"] += 1
        if calls["count"] < 5:
            raise RuntimeError("TPM (Tokens Per Minute) limit of the model is exceeded")
        return "ok"

    class FakeLLMUtils:
        generate = staticmethod(fake_generate)

    class FakeUserSimulator:
        generate = staticmethod(fake_generate)

    modules = {
        "tau2.utils.llm_utils": FakeLLMUtils,
        "tau2.user.user_simulator": FakeUserSimulator,
    }

    def fake_import_module(name):
        if name in modules:
            return modules[name]
        raise ImportError(name)

    monkeypatch.setattr(tau2_environment.importlib, "import_module", fake_import_module)
    monkeypatch.setattr(tau2_environment, "_tau2_rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(tau2_environment.time, "sleep", lambda delay: sleeps.append(delay))

    tau2_environment._install_tau2_litellm_rate_limit_retry()

    assert FakeLLMUtils.generate() == "ok"
    assert calls["count"] == 5
    assert sleeps == [1, 2, 3, 4]
    assert FakeUserSimulator.generate is FakeLLMUtils.generate


def test_tau2_litellm_generate_retry_patch_does_not_retry_non_rate_limit(monkeypatch):
    import benchmark.tau2.common.tau2_env.tau2_environment as tau2_environment

    calls = {"count": 0}

    def fake_generate():
        calls["count"] += 1
        raise RuntimeError("AuthenticationError Unauthorized")

    class FakeLLMUtils:
        generate = staticmethod(fake_generate)

    def fake_import_module(name):
        if name == "tau2.utils.llm_utils":
            return FakeLLMUtils
        raise ImportError(name)

    monkeypatch.setattr(tau2_environment.importlib, "import_module", fake_import_module)

    def fail_on_sleep(_delay):
        raise AssertionError("unexpected sleep")

    monkeypatch.setattr(tau2_environment.time, "sleep", fail_on_sleep)

    tau2_environment._install_tau2_litellm_rate_limit_retry()

    with pytest.raises(RuntimeError, match="AuthenticationError"):
        FakeLLMUtils.generate()
    assert calls["count"] == 1


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
    assert agent.tools.registered == [
        "search_experience",
        "read_experience",
        "get_user_details",
    ]


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
async def test_tau2_vikingbot_rollout_runs_on_current_event_loop():
    from benchmark.tau2.train.rollout_executor_vikingbot import VikingBotTau2RolloutExecutor

    expected_loop = asyncio.get_running_loop()
    expected_thread = threading.get_ident()
    observed = {}

    class FakeVikingBotExecutor(VikingBotTau2RolloutExecutor):
        async def _execute_one_async(self, case, context):
            del context
            observed["loop"] = asyncio.get_running_loop()
            observed["thread"] = threading.get_ident()
            await asyncio.sleep(0)
            return case.name

    executor = FakeVikingBotExecutor()

    result = await executor._execute_one(
        _case(),
        ExecutionContext(policy_snapshot_id="snapshot", metadata={}),
    )

    assert result == "case-1"
    assert observed["loop"] is expected_loop
    assert observed["thread"] == expected_thread


@pytest.mark.asyncio
async def test_tau2_prepare_experience_loader_skill_writes_required_skill(tmp_path):
    import benchmark.tau2.train.rollout_executor_vikingbot as module

    class FakeSandbox:
        def __init__(self):
            self.writes = []

        async def write_file(self, path, content):
            self.writes.append((path, content))
            target = tmp_path / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    fake_sandbox = FakeSandbox()

    class FakeSandboxManager:
        def get_workspace_path(self, session_key):
            return tmp_path

        def to_workspace_id(self, session_key):
            return "workspace"

        async def get_sandbox(self, session_key):
            return fake_sandbox

    class FakeAgent:
        sandbox_manager = FakeSandboxManager()
        context = SimpleNamespace(workspace=tmp_path)

    context_builder = await module._prepare_experience_loader_skill(
        agent=FakeAgent(),
        session_key=SimpleNamespace(),
    )

    skill_path = tmp_path / "skills" / "experience_loader" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert context_builder.workspace == tmp_path
    assert "name: experience_loader" in content
    assert "search_experience" in content
    assert "read_experience" in content
    assert fake_sandbox.writes
    assert fake_sandbox.writes[0][0] == "skills/experience_loader/SKILL.md"
    assert context_builder.latest_experience_loader_skill_content == content


@pytest.mark.asyncio
async def test_tau2_experience_loader_skill_is_required_with_relative_read_path(tmp_path):
    from vikingbot.config.schema import SessionKey

    import benchmark.tau2.train.rollout_executor_vikingbot as module

    module._write_experience_loader_files(
        workspace_path=tmp_path,
        skill_content="# experience_loader\n\nUse search_experience then read_experience.",
    )

    from vikingbot.agent.context import ContextBuilder

    context_builder = ContextBuilder(tmp_path, eval=True)
    system_prompt = await context_builder.build_system_prompt(
        SessionKey(type="cli", channel_id="tau2", chat_id="case"),
        ov_tools_enable=False,
    )

    assert "Required skill: before taking any task action" in system_prompt
    assert "`skills/experience_loader/SKILL.md`" in system_prompt
    assert "<location>skills/experience_loader/SKILL.md</location>" in system_prompt
    assert f"<location>{tmp_path}" not in system_prompt


@pytest.mark.asyncio
async def test_tau2_vikingbot_blocking_setup_and_reward_are_offloaded(monkeypatch):
    import benchmark.tau2.train.rollout_executor_vikingbot as module
    from benchmark.tau2.train.rollout_executor_vikingbot import VikingBotTau2RolloutExecutor

    event_loop_thread = threading.get_ident()
    calls = []

    class FakeEnv:
        def _get_reward(self):
            calls.append(("reward", threading.get_ident()))
            return 1.0, {"ok": True}

    class FakeTau2BenchToolProvider:
        def __init__(self, domain, task_id, data_root=None):
            self.domain = domain
            self.task_id = task_id
            self.data_root = data_root
            self.env = FakeEnv()
            self.policy = "policy"
            self.user_query = "user query"

        def reset(self):
            calls.append(("reset", threading.get_ident()))

        def list_openai_tools(self):
            return []

    class FakeAgent:
        def __init__(self):
            calls.append(("build_agent", threading.get_ident()))

    async def fake_run_agent(**kwargs):
        calls.append(("run_agent", threading.get_ident()))
        calls.append(("case_lookup", kwargs.get("case_lookup")))
        return "final", None, [], {}, 1, None, None, None

    monkeypatch.setattr(module, "_tool_provider_cls", lambda: FakeTau2BenchToolProvider)
    monkeypatch.setattr(module, "_build_agent", lambda *args, **kwargs: FakeAgent())
    monkeypatch.setattr(module, "_configure_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_run_agent", fake_run_agent)

    case = Case(
        name="tau2_case",
        task_signature="tau2:airline:train:0",
        input={
            "domain": "airline",
            "split": "train",
            "task_id": "0",
            "task_no": 0,
            "data_split": "airline_train",
        },
        rubric=Rubric(name="rubric", description="", criteria=[]),
    )
    executor = VikingBotTau2RolloutExecutor()

    rollout = await executor._execute_one(
        case,
        ExecutionContext(policy_snapshot_id="snapshot", metadata={}),
    )

    assert rollout.metadata["reward"] == 1.0
    call_values = dict(calls)
    assert call_values["case_lookup"] == {
        "benchmark": "tau2",
        "strict": True,
        "case_names": ["tau2_case", "tau2_airline_train_0"],
        "domain": "airline",
        "split": "train",
        "data_split": "airline_train",
        "task_no": 0,
        "task_id": "0",
        "case_name": "tau2_case",
        "task_signature": "tau2:airline:train:0",
        "original_case_name": None,
        "expected_fields": {
            "input.domain": "airline",
            "input.split": "train",
            "input.data_split": "airline_train",
            "input.task_no": 0,
            "input.task_id": "0",
        },
    }
    call_threads = call_values
    assert call_threads["reset"] != event_loop_thread
    assert call_threads["build_agent"] != event_loop_thread
    assert call_threads["reward"] != event_loop_thread
    assert call_threads["run_agent"] == event_loop_thread


@pytest.mark.asyncio
async def test_tau2_run_agent_force_loads_experience_loader_skill_before_task_actions(monkeypatch):
    from vikingbot.providers.base import LLMResponse, ToolCallRequest

    import benchmark.tau2.train.rollout_executor_vikingbot as module

    observed = {}
    real_imports = module._vikingbot_imports()

    class FakeSandbox:
        content = ""

        async def read_file(self, path):
            observed.setdefault("sandbox_reads", []).append(path)
            return self.content

        async def write_file(self, path, content):
            observed.setdefault("sandbox_writes", []).append((path, content))
            type(self).content = content

    class FakeSandboxManager:
        def get_workspace_path(self, session_key):
            return Path("/tmp/fake-workspace")

        def to_workspace_id(self, session_key):
            return "workspace"

        async def get_sandbox(self, session_key):
            return FakeSandbox()

    class FakeContextBuilder:
        def __init__(self, workspace, *, sandbox_manager=None, eval=False, **kwargs):
            self.workspace = workspace
            self.sandbox_manager = sandbox_manager
            self.latest_experience_loader_skill_content = ""

        async def build_messages(self, **kwargs):
            return [
                {"role": "system", "content": "ctx system"},
                {"role": "user", "content": kwargs["current_message"]},
            ]

        def add_assistant_message(
            self, messages, content, tool_calls=None, reasoning_content=None
        ):
            msg = {"role": "assistant", "content": content or "[tool call]"}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if reasoning_content:
                msg["reasoning_content"] = reasoning_content
            messages.append(msg)
            return messages

        def add_tool_result(self, messages, tool_call_id, tool_name, result):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": result,
                }
            )
            return messages

    class FakeProvider:
        async def chat(self, messages, tools=None, **kwargs):
            observed["llm_messages"] = list(messages)
            return LLMResponse(
                content=None,
                tool_calls=[ToolCallRequest("call-1", "done", {}, 0)],
            )

        async def chat_stream(self, **kwargs):
            from vikingbot.providers.base import LLMStreamEvent

            yield LLMStreamEvent(type="response", response=await self.chat(**kwargs))

        def get_default_model(self):
            return "fake"

    class FakeAgent:
        def __init__(self):
            from vikingbot.agent.tools.filesystem import ReadFileTool
            from vikingbot.agent.tools.registry import ToolRegistry

            self.sandbox_manager = FakeSandboxManager()
            self.context = FakeContextBuilder(Path("/tmp/fake-workspace"))
            self.tools = ToolRegistry()
            self.tools.register(ReadFileTool())
            self.tools.register(_DoneTool())
            self.provider = FakeProvider()
            self.model = "fake"
            self.temperature = None
            self.max_iterations = 1

        _chat_with_stream_events = real_imports["AgentLoop"]._chat_with_stream_events
        _run_agent_loop = real_imports["AgentLoop"]._run_agent_loop

    class _DoneTool:
        @property
        def name(self):
            return "done"

        @property
        def description(self):
            return "done"

        @property
        def parameters(self):
            return {"type": "object", "properties": {}}

        def to_schema(self):
            return {
                "type": "function",
                "function": {
                    "name": self.name,
                    "description": self.description,
                    "parameters": self.parameters,
                },
            }

        def validate_params(self, params):
            return []

        async def execute(self, tool_context, **kwargs):
            return ""

    monkeypatch.setattr(
        module,
        "_vikingbot_imports",
        lambda: {**real_imports, "ContextBuilder": FakeContextBuilder},
    )

    result = await module._run_agent(
        agent=FakeAgent(),
        system_prompt="tau2 policy",
        user_prompt="user query",
        session_key=SimpleNamespace(safe_name=lambda: "session"),
        sender_id="tau2_user",
        keep_default_tools=True,
        case_lookup={"benchmark": "tau2", "strict": True, "case_name": "case"},
    )

    tools_used = result[2]
    messages = observed["llm_messages"]
    read_call_index = next(
        i
        for i, msg in enumerate(messages)
        if msg.get("role") == "assistant" and "read_file" in str(msg.get("tool_calls"))
    )
    tool_result_index = next(
        i
        for i, msg in enumerate(messages)
        if msg.get("role") == "tool" and msg.get("name") == "read_file"
    )

    assert observed["sandbox_writes"][0][0] == "skills/experience_loader/SKILL.md"
    assert observed["sandbox_reads"] == ["skills/experience_loader/SKILL.md"]
    assert read_call_index < tool_result_index
    assert "search_experience" in messages[tool_result_index]["content"]
    assert "read_experience" in messages[tool_result_index]["content"]
    assert tools_used[0]["tool_name"] == "read_file"
    assert tools_used[0]["required_skill"] == "experience_loader"
