# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
import threading
from importlib import import_module
from types import SimpleNamespace
from typing import Any

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


@pytest.fixture
def airline_case(tmp_path):
    from benchmark.tau2.train.case_loader import Tau2CaseLoader

    domain_dir = tmp_path / "domains" / "airline"
    domain_dir.mkdir(parents=True)
    (domain_dir / "split_tasks.json").write_text(json.dumps({"test": ["0", "1"]}))
    [case] = Tau2CaseLoader(
        domain="airline", split="test", data_root=str(tmp_path), task_indices=[1]
    ).load_cases()
    return case


@pytest.mark.asyncio
async def test_tau2_commit_request_and_trajectory_prompt_keep_tool_evidence(
    monkeypatch, tmp_path, airline_case
):
    import httpx

    from benchmark.tau2.train.rollout_executor_vikingbot import (
        _build_rollout_messages,
        _tau2_evaluation,
    )
    from openviking.message import Message, ToolPart
    from openviking.session.memory.agent_trajectory_context_provider import (
        AgentTrajectoryContextProvider,
    )
    from openviking.session.retention import is_user_query
    from openviking.session.train import Rollout, SessionCommitPolicyTrainer
    from openviking_cli.client.http import AsyncHTTPClient

    monkeypatch.setenv("OPENVIKING_CLI_CONFIG_FILE", str(tmp_path / "missing.conf"))
    http_client = httpx.AsyncClient
    session_id = ""
    uploaded: list[dict[str, Any]] = []

    def handle(request):
        nonlocal session_id, uploaded
        path = request.url.path
        if path == "/api/v1/sessions":
            session_id = json.loads(request.content)["session_id"]
            result = {"session_id": session_id}
        elif path == f"/api/v1/sessions/{session_id}/messages/batch":
            uploaded = json.loads(request.content)["messages"]
            result = {"added": len(uploaded)}
        elif path == f"/api/v1/sessions/{session_id}/commit":
            assert uploaded
            result = {"status": "accepted", "task_id": "commit-1"}
        else:
            assert path == "/api/v1/tasks/commit-1"
            result = {"status": "completed"}
        return httpx.Response(200, json={"status": "ok", "result": result})

    monkeypatch.setattr(
        "openviking_sdk.client.httpx.AsyncClient",
        lambda **kwargs: http_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    reward = {"reward": 0.0}
    messages = _build_rollout_messages(
        system_prompt="Check the itinerary before making any change.",
        user_prompt=airline_case.input["user_query"],
        tools_used=[
            {
                "tool_name": "communicate_with_user",
                "args": {"content": "May I look up your reservation?"},
                "result": "Yes, please proceed.",
            },
            {
                "tool_name": "get_reservation_details",
                "args": '{"reservation_id": "Q69X3R"}',
                "result": '{"reservation_id": "Q69X3R", "flights": []}',
            },
            {"tool_name": "done", "args": "{}", "result": "Task Terminated"},
        ],
        final_content=None,
        evaluation_result=reward,
        reward=0.0,
    )
    rollout = Rollout(
        case=airline_case,
        messages=messages,
        policy_snapshot_id="snapshot",
        evaluation=_tau2_evaluation(reward=0.0, evaluation_result=reward),
    )
    client = AsyncHTTPClient(url="http://openviking.test")
    try:
        await client.initialize()
        training = await SessionCommitPolicyTrainer(client, run_id="tau2-audit").train_rollouts(
            [rollout], _policy_set()
        )
    finally:
        await client.close()

    [commit] = training.apply_result.metadata["commit_results"]
    assert (commit["session_id"], commit["task_id"], commit["task_status"]) == (
        session_id,
        "commit-1",
        "completed",
    )
    assert messages[-1].id == "tau2-reward"
    assert all(message.id != "tau2-final" for message in messages)
    restored = [
        Message.from_dict({"id": str(index), **request}) for index, request in enumerate(uploaded)
    ]
    [lookup, done] = [
        message for message in restored if any(isinstance(part, ToolPart) for part in message.parts)
    ]
    part = lookup.parts[0]
    assert isinstance(part, ToolPart)
    assert (lookup.role, part.tool_name, part.tool_status) == (
        "assistant",
        "get_reservation_details",
        "completed",
    )
    assert part.tool_input == {"reservation_id": "Q69X3R"}
    assert json.loads(part.tool_output or "") == {"reservation_id": "Q69X3R", "flights": []}
    assert not is_user_query(lookup)
    assert not is_user_query(done)
    [conversation] = await AgentTrajectoryContextProvider(messages=restored).prefetch()
    assert (
        "[assistant][assistant]: ToolCall: tool_name=get_reservation_details"
        in conversation["content"]
    )
    assert '"reservation_id": "Q69X3R"' in conversation["content"]
    assert "[user][user]: Yes, please proceed." in conversation["content"]


def test_tau2_reward_info_is_json_safe_in_rollout_messages_and_evaluation():
    import json

    from tau2.data_model.simulation import RewardInfo, RewardType

    from benchmark.tau2.train.rollout_executor_vikingbot import (
        _build_rollout_messages,
        _tau2_evaluation,
    )

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
    from litellm import ModelResponse
    from tau2.data_model.message import UserMessage
    from tau2.user import user_simulator
    from tau2.utils import llm_utils

    from benchmark.tau2.common.tau2_env import tau2_environment

    response = ModelResponse(
        model="gpt-4o-mini",
        choices=[{"message": {"role": "assistant", "content": "Yes, please."}}],
    )
    rate_limit = "TPM (Tokens Per Minute) limit of the model is exceeded"
    auth_error = RuntimeError("AuthenticationError Unauthorized")
    outcomes = iter([*(RuntimeError(rate_limit) for _ in range(4)), response, auth_error])
    query = "Please confirm your booking."
    waits = iter([1, 2, 3, 4])

    def completion(*, model, messages, **kwargs):
        assert model == "gpt-4o-mini"
        assert messages[-1]["content"] == query
        result = next(outcomes)
        if isinstance(result, Exception):
            raise result
        return result

    def wait(delay):
        assert delay == next(waits)

    monkeypatch.setattr(llm_utils, "completion", completion)
    # The installer mutates aliases held by the imported tau2 modules.
    monkeypatch.setattr(llm_utils, "generate", llm_utils.generate)
    monkeypatch.setattr(llm_utils, "_openviking_original_generate", None, raising=False)
    for name in tau2_environment._TAU2_GENERATE_REFERENCE_MODULES:
        module = import_module(name)
        monkeypatch.setattr(module, "generate", module.generate)
    monkeypatch.setattr(tau2_environment, "_tau2_rate_limit_retry_delay", lambda attempt: attempt)
    monkeypatch.setattr(tau2_environment, "time", SimpleNamespace(sleep=wait))
    tau2_environment._install_tau2_litellm_rate_limit_retry()

    messages = [UserMessage(role="user", content=query)]
    assert user_simulator.generate(model="gpt-4o-mini", messages=messages).content == "Yes, please."
    assert next(waits, None) is None
    with pytest.raises(RuntimeError, match="AuthenticationError") as failure:
        user_simulator.generate(model="gpt-4o-mini", messages=messages)
    assert failure.value is auth_error


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


@pytest.fixture
def exact_case_client(monkeypatch):
    from vikingbot.openviking_mount.ov_server import VikingClient

    class FakeClient:
        def __init__(self):
            self.files = {}
            self.reads = []
            self.closed = False

        def _memory_target_uri(self, user_id):
            assert user_id is None
            return "viking://user/u/memories"

        async def read_content(self, uri, *, level):
            assert level == "read"
            self.reads.append(uri)
            value = self.files.get(uri, "")
            if isinstance(value, Exception):
                raise value
            return value

        async def search(self, *args, **kwargs):
            pytest.fail("Exact task recall must never call relevance search")

        async def close(self):
            self.closed = True

    client = FakeClient()

    async def create(**kwargs):
        return client

    monkeypatch.setattr(VikingClient, "create", create)
    return client


@pytest.mark.asyncio
async def test_tau2_experience_lookup_uses_exact_signature_not_query(exact_case_client):
    from vikingbot.agent.tools.registry import ToolRegistry

    from benchmark.tau2.train.rollout_executor_vikingbot import (
        _configure_tools,
        _tau2_case_lookup,
    )

    case = Case(
        name="tau2_airline_train_0",
        task_signature="tau2:airline:train:0",
        input={
            "domain": "airline",
            "split": "train",
            "data_split": "airline_train",
            "task_id": "0",
            "task_no": 0,
        },
        rubric=Rubric(name="rubric", description="", criteria=[]),
    )
    case_uri = "viking://user/u/memories/cases/tau2_airline_train_0.md"
    exp_uri = "viking://user/u/memories/experiences/cancel.md"
    exact_case_client.files[case_uri] = (
        f"# {case.name}\n\n## Task Signature\n{case.task_signature}\n\n"
        f"## Input\n{json.dumps(case.input)}\n\n"
        "## Linked Experiences\n- [cancel](../experiences/cancel.md)\n"
    )
    exact_case_client.files[exp_uri] = "## Situation\nCancel an eligible reservation.\n"
    # Training trials use the original case link, even with a different per-trial signature.
    case.input["original_case_name"] = case.name
    case.input["train_trial"] = 7
    case.name += "_trial_7"
    case.task_signature += ":trial:7"
    agent = SimpleNamespace(tools=ToolRegistry())
    for name in (
        "read_file",
        "exec",
        "web_search",
        "openviking_memory_commit",
        "mcp_external_tool",
    ):
        agent.tools.register(SimpleNamespace(name=name))
    task_tools = [
        {
            "type": "function",
            "function": {
                "name": "get_user_details",
                "description": "Get user",
                "parameters": {"type": "object"},
            },
        },
    ]
    _configure_tools(
        agent,
        SimpleNamespace(list_openai_tools=lambda: task_tools),
        keep_default_tools=True,
        case_lookup=_tau2_case_lookup(case),
    )
    assert set(agent.tools.tool_names) == {
        "read_file",
        "exec",
        "search_experience",
        "read_experience",
        "get_user_details",
    }
    tool = agent.tools.get("search_experience")

    canonical_signature = "tau2:airline:train:0"
    for query in ["cancel current reservation", "unrelated retail task 47"]:
        result = json.loads(
            await tool.execute(None, query=query, task_signature="tau2:airline:train:47")
        )
        assert result["match_type"] == "task_signature_exact"
        assert result["task_signature"] == canonical_signature
        assert result["count"] == 1
        candidate = result["candidates"][0]
        assert candidate["case_uri"] == case_uri
        assert candidate["task_signature"] == canonical_signature
        assert candidate["experiences"] == [
            {
                "index": 1,
                "name": "cancel",
                "uri": exp_uri,
                "situation": "Cancel an eligible reservation.",
            }
        ]
    assert exact_case_client.closed
    assert exact_case_client.reads == [case_uri, exp_uri, case_uri, exp_uri]


@pytest.mark.asyncio
async def test_tau2_exact_case_miss_never_falls_back(exact_case_client):
    from benchmark.tau2.train.rollout_executor_vikingbot import _make_search_experience_tool

    case_uri = "viking://user/u/memories/cases/tau2_airline_train_0.md"
    tool = _make_search_experience_tool(
        case_lookup={
            "case_name": "tau2_airline_train_0",
            "task_signature": "tau2:airline:train:0",
        }
    )
    for value in (
        "# tau2_airline_train_0\n\n## Task Signature\ntau2:airline:test:0\n"
        "\n## Linked Experiences\n- [wrong](../experiences/wrong.md)\n",
        OSError("read failed"),
    ):
        exact_case_client.files[case_uri] = value
        result = json.loads(await tool.execute(None, query="similar task"))
        assert (result["count"], result["candidates"]) == (0, [])

    no_identity = json.loads(await _make_search_experience_tool().execute(None, query="cancel"))
    assert (no_identity["count"], no_identity["candidates"]) == (0, [])
    assert exact_case_client.reads == [case_uri, case_uri]
    assert exact_case_client.closed


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

    monkeypatch.setenv("TAU2_AGENT_LLM", "openai/test-agent")
    monkeypatch.setenv("TAU2_USER_LLM", "openai/test-user")
    agent_llm, _, user_llm, _ = _resolve_llm_runtime_config(NativeTau2RolloutExecutor())
    assert (agent_llm, user_llm) == ("openai/test-agent", "openai/test-user")


@pytest.mark.asyncio
async def test_tau2_service_rollout_backend_option_overrides_default(monkeypatch, airline_case):
    import httpx

    import benchmark.tau2.train.service_app as service_app
    from benchmark.tau2.train.rollout_executor import (
        NativeTau2RolloutExecutor,
        VikingBotTau2RolloutExecutor,
    )
    from openviking.message import Message, TextPart
    from openviking.session.train import Rollout
    from openviking.session.train.components.dataset_service import case_to_dict

    selected: list[NativeTau2RolloutExecutor | VikingBotTau2RolloutExecutor] = []

    async def execute(executor, cases, policy_set, context):
        selected.append(executor)
        return [
            Rollout(
                case=cases[0],
                policy_snapshot_id=context.policy_snapshot_id,
                messages=[
                    Message(id="reply", role="assistant", parts=[TextPart(text="offline reply")])
                ],
            )
        ]

    for executor_class in (NativeTau2RolloutExecutor, VikingBotTau2RolloutExecutor):
        monkeypatch.setattr(executor_class, "execute", execute)
    app = service_app.create_app(rollout_backend="native")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://tau2.test"
    ) as client:

        async def get_outcome(execution_url):
            while True:
                status = await client.get(execution_url)
                status.raise_for_status()
                result = status.json()
                if result["status"] not in {"pending", "running"}:
                    return result
                await asyncio.sleep(0)

        for options in (
            {"keep_default_tools": False, "max_iterations": 7},
            {"rollout_backend": "vikingbot", "max_iterations": 5, "rollout_language": "zh"},
            {"show_progress": True, "max_steps": 11},
        ):
            response = await client.post(
                "/v1/rollouts/execute",
                json={
                    "case": case_to_dict(airline_case),
                    "policy_set": {"root_uri": _policy_set().root_uri},
                    "execution_context": {"policy_snapshot_id": "snapshot"},
                    "options": options,
                },
            )
            response.raise_for_status()
            execution_url = f"/v1/rollouts/executions/{response.json()['execution_id']}"

            result = await asyncio.wait_for(get_outcome(execution_url), timeout=5)
            assert result["status"] == "completed", result
            rollout = result["rollout"]
            assert rollout["case"]["name"] == airline_case.name
            assert rollout["policy_snapshot_id"] == "snapshot"

    native, vikingbot, verbose = selected
    assert isinstance(native, NativeTau2RolloutExecutor)
    assert (native.max_steps, native.memory_enabled, native.show_progress) == (7, False, False)
    assert isinstance(vikingbot, VikingBotTau2RolloutExecutor)
    assert (vikingbot.max_iterations, vikingbot.rollout_language) == (5, "zh")
    assert isinstance(verbose, NativeTau2RolloutExecutor)
    assert (verbose.max_steps, verbose.show_progress) == (11, True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "trial, run_error", [(None, None), (0, RuntimeError), (9, asyncio.CancelledError)]
)
async def test_tau2_vikingbot_blocking_setup_and_reward_are_offloaded(
    monkeypatch, trial, run_error
):
    import benchmark.tau2.train.rollout_executor_vikingbot as module
    from benchmark.tau2.train.rollout_executor_vikingbot import VikingBotTau2RolloutExecutor

    event_loop_thread = threading.get_ident()
    event_loop = asyncio.get_running_loop()
    calls: list[tuple[str, Any]] = []

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

        def close(self, *, termination_reason):
            calls.append(("close", threading.get_ident()))
            calls.append(("termination_reason", termination_reason))

        def list_openai_tools(self):
            return []

    class FakeAgent:
        def __init__(self):
            calls.append(("build_agent", threading.get_ident()))

    async def fake_run_agent(**kwargs):
        assert asyncio.get_running_loop() is event_loop
        if run_error is not None:
            raise run_error("agent failed")
        tools_used = [
            {
                "tool_name": "get_user_details",
                "args": {"user_id": "user-1"},
                "result": '{"user_id": "user-1"}',
            }
        ]
        return "final", None, tools_used, {}, 1, None, None, None

    monkeypatch.setattr(module, "_tool_provider_cls", lambda: FakeTau2BenchToolProvider)
    monkeypatch.setattr(module, "_build_agent", lambda *args, **kwargs: FakeAgent())
    monkeypatch.setattr(module, "_configure_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_run_agent", fake_run_agent)
    monkeypatch.setattr(module, "_vikingbot_imports", lambda: {"SessionKey": SimpleNamespace})

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
    if trial is not None:
        case.name = f"tau2_case_trial_{trial}"
        case.task_signature += f":trial:{trial}"
        case.input.update({"original_case_name": "tau2_case", "train_trial": trial})
    executor = VikingBotTau2RolloutExecutor()

    if run_error is not None:
        with pytest.raises(run_error, match="agent failed"):
            await executor._execute_one(
                case, ExecutionContext(policy_snapshot_id="snapshot", metadata={})
            )
        assert dict(calls)["termination_reason"] == "agent_error"
        assert dict(calls)["close"] != event_loop_thread
        assert "reward" not in dict(calls)
        return

    rollout = await executor._execute_one(
        case,
        ExecutionContext(policy_snapshot_id="snapshot", metadata={}),
    )

    assert rollout.metadata["reward"] == 1.0
    assert rollout.evaluation is not None
    assert rollout.evaluation.score == 1.0
    assert rollout.evaluation.metadata["evaluation_result"] == {"ok": True}
    assert dict(calls)["termination_reason"] == "agent_stop"
    assert [name for name, value in calls if isinstance(value, int)] == [
        "reset",
        "build_agent",
        "close",
        "reward",
    ]
    assert all(value != event_loop_thread for _, value in calls if isinstance(value, int))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "user_reply, done_result, terminated, hard_stop",
    [
        ("Yes. ###STOP###", "", True, False),
        ("###TRANSFER###", "", True, True),
        ("###OUT-OF-SCOPE###", "", True, True),
        ("Task Terminated", "", True, True),
        ("Task Terminated? Please continue.", "Error: done failed", False, False),
        ("Error: User dialogue is closed.", "", True, False),
    ],
)
async def test_tau2_run_agent_force_loads_experience_loader_skill_before_task_actions(
    monkeypatch, tmp_path, user_reply, done_result, terminated, hard_stop
):
    from vikingbot.agent.loop import AgentLoop
    from vikingbot.agent.tools.filesystem import ReadFileTool
    from vikingbot.bus.queue import MessageBus
    from vikingbot.config.schema import Config, SessionKey
    from vikingbot.providers.base import LLMResponse, LLMStreamEvent, ToolCallRequest

    import benchmark.tau2.train.rollout_executor_vikingbot as module
    from benchmark.tau2.common.tau2_env.tau2_environment import Tau2BenchEnv

    model_inputs: list[list[dict[str, Any]]] = []
    workspace = tmp_path / "workspace"
    reads: list[str] = []

    class LocalSandbox:
        def get_workspace_path(self, session_key):
            return workspace

        def to_workspace_id(self, session_key):
            return "offline-workspace"

        async def get_sandbox_cwd(self, session_key):
            return "/workspace"

        async def get_sandbox(self, session_key):
            return self

        async def read_file(self, path):
            reads.append(path)
            return (workspace / path).read_text()

        async def write_file(self, path, content):
            destination = workspace / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)

    class Model:
        async def chat(self, messages, **kwargs):
            model_inputs.append(list(messages))
            if len(model_inputs) == 1:
                return LLMResponse(content="Please confirm.")
            return LLMResponse(content=None, tool_calls=[ToolCallRequest("call-1", "done", {}, 0)])

        async def chat_stream(self, **kwargs):
            yield LLMStreamEvent(type="response", response=await self.chat(**kwargs))

        def get_default_model(self):
            return "fake"

    env = Tau2BenchEnv.__new__(Tau2BenchEnv)
    monkeypatch.setattr(
        env,
        "_impl",
        SimpleNamespace(terminated=terminated, tool_call=lambda *args: done_result),
        raising=False,
    )

    class Provider:
        def list_openai_tools(self):
            return [
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": name,
                        "parameters": {"type": "object"},
                    },
                }
                for name in ("done", "communicate_with_user")
            ]

        def call_tool(self, name, arguments):
            if name == "communicate_with_user":
                return user_reply
            return env.tool_call(name, arguments)

    monkeypatch.setattr(AgentLoop, "_register_default_tools", lambda self: None)
    agent = AgentLoop(
        bus=MessageBus(),
        provider=Model(),
        workspace=workspace,
        max_iterations=3,
        sandbox_manager=LocalSandbox(),
        config=Config(storage_workspace=str(tmp_path), hooks=[]),
    )
    agent.tools.register(ReadFileTool())
    module._configure_tools(agent, Provider(), keep_default_tools=True)

    result = await module._run_agent(
        agent=agent,
        system_prompt="tau2 policy",
        user_prompt="user query",
        session_key=SessionKey(type="cli", channel_id="tau2", chat_id="case"),
        sender_id="tau2_user",
        keep_default_tools=True,
    )

    skill = "skills/experience_loader/SKILL.md"
    assert reads == [skill]
    assert "name: experience_loader" in (workspace / skill).read_text()
    first_messages = model_inputs[0]
    assert f"<location>{skill}</location>" in first_messages[0]["content"]
    assert "Required skill: before taking any task action" in first_messages[0]["content"]
    assert first_messages[-1]["role"] == "tool"
    assert first_messages[-1]["name"] == "read_file"
    assert "search_experience" in first_messages[-1]["content"]
    assert "read_experience" in first_messages[-1]["content"]

    tools = result[2]
    assert tools[0]["required_skill"] == "experience_loader"
    assert tools[1]["result"] == user_reply
    if hard_stop:
        assert len(model_inputs) == 1
        assert all(tool["tool_name"] != "done" for tool in tools)
    else:
        reply_role = "system" if user_reply.startswith("Error:") else "user"
        assert {"role": reply_role, "content": user_reply} in model_inputs[1]
        done = [tool for tool in tools if tool["tool_name"] == "done"]
        expected = done_result or "Task Terminated"
        assert done
        assert all(tool["result"] == expected for tool in done)
        assert all(tool["execute_success"] is (not expected.startswith("Error:")) for tool in done)
