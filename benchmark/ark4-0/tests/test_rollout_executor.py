from __future__ import annotations

import asyncio
from typing import Any

import pytest
from rollout_executor import ArkRolloutExecutor, RolloutBatchCoordinator

from openviking.message import ToolPart
from openviking.session.train import Case, ExecutionContext, ExperienceSet, Rubric, RubricCriterion


class FakeRolloutClient:
    def __init__(self, *, messages: list[dict[str, Any]] | None = None) -> None:
        self.messages = messages if messages is not None else []
        self.idempotency_keys: list[str] = []
        self.submitted_bodies: list[dict[str, Any]] = []

    async def submit_rollout_eval(
        self,
        task_id: str,
        *,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        assert task_id == "task-1"
        assert body["case_ids"] == ["case-1"]
        assert body["connector_config"]["options"]["repeat"] == 1
        self.submitted_bodies.append(body)
        self.idempotency_keys.append(idempotency_key)
        return {
            "batch_rollout_id": "batch-1",
            "case_rollouts": [{"case_id": "case-1", "case_rollout_id": "case-rollout-1"}],
        }

    async def get_case_rollout(self, task_id: str, case_rollout_id: str) -> dict[str, Any]:
        assert task_id == "task-1"
        assert case_rollout_id == "case-rollout-1"
        return {
            "request_id": "request-1",
            "status": "completed",
            "completion_seq": 1,
            "result": {
                "final_answer": "done",
                "messages": self.messages,
                "evaluation": {
                    "passed": "false",
                    "score": 0.25,
                    "fail_reason": "missing required detail",
                },
                "evaluator_status": "succeeded",
                "trace_status": "succeeded",
                "artifacts": [{"name": "answer.txt", "uri": "tos://answer"}],
                "result_uri": "tos://result",
            },
        }


class FakeBatchClient:
    def __init__(self) -> None:
        self.submitted_bodies: list[dict[str, Any]] = []

    async def submit_rollout_eval(
        self,
        task_id: str,
        *,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        assert task_id == "task-1"
        assert idempotency_key
        self.submitted_bodies.append(body)
        return {
            "batch_rollout_id": "batch-viking",
            "case_rollouts": [
                {"case_id": case_id, "case_rollout_id": f"rollout-{case_id}"}
                for case_id in body["case_ids"]
            ],
        }

    async def get_case_rollout(
        self,
        task_id: str,
        case_rollout_id: str,
    ) -> dict[str, Any]:
        assert task_id == "task-1"
        case_id = case_rollout_id.removeprefix("rollout-")
        return {
            "request_id": f"request-{case_id}",
            "status": "completed",
            "completion_seq": 1,
            "result": {
                "final_answer": "done",
                "messages": [
                    {"id": f"{case_id}-u", "role": "user", "content": "please do it"},
                    {"id": f"{case_id}-a", "role": "assistant", "content": "done"},
                ],
                "evaluation": {"passed": True, "score": 1.0},
            },
        }


def make_case() -> Case:
    return Case(
        name="case one",
        task_signature="case-one-signature",
        input={"task_id": "case-1", "user_query": "please do it"},
        rubric=Rubric(
            name="platform",
            description="",
            criteria=[
                RubricCriterion(
                    name="platform",
                    description="",
                    required=True,
                    weight=1.0,
                )
            ],
        ),
        metadata={"platform_case_id": "case-1"},
    )


def make_viking_case() -> Case:
    case = make_case()
    case.metadata["viking_phase"] = "train"
    return case


def make_second_case() -> Case:
    case = make_case()
    case.name = "case two"
    case.task_signature = "case-two-signature"
    case.input["task_id"] = "case-2"
    case.metadata["platform_case_id"] = "case-2"
    return case


def make_second_viking_case() -> Case:
    case = make_second_case()
    case.metadata["viking_phase"] = "train"
    return case


@pytest.mark.asyncio
async def test_executor_maps_openai_messages_tools_and_evaluation() -> None:
    client = FakeRolloutClient(
        messages=[
            {"id": "m1", "role": "user", "content": "please do it"},
            {
                "id": "m2",
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "tool-1",
                        "function": {"name": "lookup", "arguments": '{"id":"1"}'},
                        "output": '{"ok":true}',
                    }
                ],
            },
            {"id": "m3", "role": "assistant", "content": "done"},
        ]
    )
    executor = ArkRolloutExecutor(
        client=client,  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
        idempotency_namespace="test-run",
    )

    rollouts = await executor.execute(
        [make_case()],
        ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
        ExecutionContext(
            policy_snapshot_id="snapshot-1",
            metadata={"training": True, "epoch": 0, "stage": "train_rollout"},
        ),
    )

    assert len(rollouts) == 1
    rollout = rollouts[0]
    assert rollout.evaluation is not None
    assert rollout.evaluation.passed is False
    assert rollout.evaluation.score == 0.25
    assert rollout.evaluation.feedback == ["missing required detail"]
    assert rollout.metadata["platform_case_rollout_id"] == "case-rollout-1"
    assert any(isinstance(part, ToolPart) for message in rollout.messages for part in message.parts)
    assert len(client.idempotency_keys) == 1
    assert client.idempotency_keys[0].startswith("test-run:")


@pytest.mark.asyncio
async def test_executor_rejects_empty_training_trajectory_by_default() -> None:
    executor = ArkRolloutExecutor(
        client=FakeRolloutClient(),  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError, match="contains no usable messages"):
        await executor.execute(
            [make_case()],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            ExecutionContext(
                policy_snapshot_id="snapshot-1",
                metadata={"training": True, "epoch": 0},
            ),
        )


@pytest.mark.asyncio
async def test_executor_sends_viking_phase() -> None:
    client = FakeRolloutClient(
        messages=[
            {"id": "m1", "role": "user", "content": "please do it"},
            {"id": "m2", "role": "assistant", "content": "done"},
        ]
    )
    executor = ArkRolloutExecutor(
        client=client,  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    await executor.execute(
        [make_viking_case()],
        ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
        ExecutionContext(policy_snapshot_id="snapshot-1", metadata={"training": True}),
    )

    assert client.submitted_bodies[0]["phase"] == "train"


@pytest.mark.asyncio
async def test_executor_submits_one_viking_batch_for_the_whole_phase() -> None:
    client = FakeBatchClient()
    executor = ArkRolloutExecutor(
        client=client,  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
        concurrency=2,
    )

    rollouts = await executor.execute(
        [make_viking_case(), make_second_viking_case()],
        ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
        ExecutionContext(policy_snapshot_id="snapshot-1", metadata={"training": True}),
    )

    assert len(client.submitted_bodies) == 1
    assert client.submitted_bodies[0]["case_ids"] == ["case-1", "case-2"]
    assert client.submitted_bodies[0]["phase"] == "train"
    assert client.submitted_bodies[0]["workers"] == 2
    assert [rollout.case.name for rollout in rollouts] == ["case one", "case two"]


@pytest.mark.asyncio
async def test_per_case_calls_share_the_batch_recorded_by_case_query() -> None:
    client = FakeBatchClient()
    coordinator = RolloutBatchCoordinator()
    first = make_case()
    second = make_second_case()
    descriptor = {
        "batch_id": "query-batch-1",
        "case_ids": ["case-1", "case-2"],
    }
    first.metadata["_ark_rollout_batch"] = descriptor
    second.metadata["_ark_rollout_batch"] = descriptor

    def executor() -> ArkRolloutExecutor:
        return ArkRolloutExecutor(
            client=client,  # type: ignore[arg-type]
            platform_task_id="task-1",
            poll_interval_seconds=0.001,
            timeout_seconds=1,
            concurrency=2,
            batch_coordinator=coordinator,
        )

    context = ExecutionContext(
        policy_snapshot_id="snapshot-1",
        metadata={"training": True, "epoch": 0, "stage": "train_rollout"},
    )
    first_result, second_result = await asyncio.gather(
        executor().execute(
            [first],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            context,
        ),
        executor().execute(
            [second],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            context,
        ),
    )

    assert len(client.submitted_bodies) == 1
    assert client.submitted_bodies[0]["case_ids"] == ["case-1", "case-2"]
    assert "phase" not in client.submitted_bodies[0]
    assert first_result[0].case.name == "case one"
    assert second_result[0].case.name == "case two"


@pytest.mark.asyncio
async def test_eval_trials_create_distinct_batches() -> None:
    client = FakeBatchClient()
    coordinator = RolloutBatchCoordinator()
    descriptor = {
        "batch_id": "eval-query-batch",
        "case_ids": ["case-1"],
    }
    cases = []
    for trial in (0, 1):
        case = make_viking_case()
        case.metadata["viking_phase"] = "validation"
        case.metadata["eval_trial"] = trial
        case.metadata["_ark_rollout_batch"] = descriptor
        cases.append(case)

    executor = ArkRolloutExecutor(
        client=client,  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
        concurrency=2,
        batch_coordinator=coordinator,
    )
    context = ExecutionContext(
        policy_snapshot_id="snapshot-1",
        metadata={"training": False, "stage": "final_eval"},
    )
    await asyncio.gather(
        *(executor.execute(
            [case],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            context,
        ) for case in cases)
    )

    assert len(client.submitted_bodies) == 2
    assert all(body["phase"] == "validation" for body in client.submitted_bodies)


@pytest.mark.asyncio
async def test_executor_can_synthesize_messages_when_explicitly_enabled() -> None:
    executor = ArkRolloutExecutor(
        client=FakeRolloutClient(),  # type: ignore[arg-type]
        platform_task_id="task-1",
        poll_interval_seconds=0.001,
        timeout_seconds=1,
        require_messages_for_training=False,
    )

    rollout = (
        await executor.execute(
            [make_case()],
            ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
            ExecutionContext(
                policy_snapshot_id="snapshot-1",
                metadata={"training": True, "epoch": 0},
            ),
        )
    )[0]

    assert [message.role for message in rollout.messages] == ["user", "assistant"]


@pytest.mark.asyncio
@pytest.mark.parametrize("batch", [False, True], ids=["single", "batch"])
async def test_executor_forwards_memory_runtime_and_allowed_headers(batch: bool) -> None:
    client = FakeBatchClient()
    executor = ArkRolloutExecutor(
        client=client,  # type: ignore[arg-type]
        platform_task_id="task-1",
        runtime_params={
            "memory": {
                "enabled": True,
                "mode": "read_only",
                "openviking_target": "ov-ark-test",
            }
        },
        extra_header={
            "x-vaka-request-source": "ark-lx",
        },
        poll_interval_seconds=0.001,
        timeout_seconds=1,
    )

    await executor.execute(
        [make_viking_case(), make_second_viking_case()] if batch else [make_case()],
        ExperienceSet(root_uri="viking://user/memories/experiences", policies=[]),
        ExecutionContext(policy_snapshot_id="snapshot-1", metadata={"training": True}),
    )

    assert len(client.submitted_bodies) == 1
    body = client.submitted_bodies[0]
    assert body["case_ids"] == (["case-1", "case-2"] if batch else ["case-1"])
    assert body["runtime_params"] == {
        "memory": {
            "enabled": True,
            "mode": "read_only",
            "openviking_target": "ov-ark-test",
        }
    }
    assert body["extra_header"] == {
        "x-vaka-request-source": "ark-lx",
    }
    assert "x-tt-backend" not in body["extra_header"]
