from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import httpx
import pytest
from service_app import ArkAdapterServiceConfig, create_app

from openviking.session.train import Case, ExperienceSet, Rubric, RubricCriterion
from openviking.session.train.components.dataset_service import case_to_dict, policy_set_to_dict


def make_case() -> Case:
    return Case(
        name="adapter case",
        task_signature="adapter-case-signature",
        input={"task_id": "case-1", "user_query": "hello"},
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


class CompletedPlatformClient:
    def __init__(self) -> None:
        self.completed = False
        self.created_count = 0
        self.created_bodies: list[dict[str, Any]] = []
        self.submitted_rollouts: list[dict[str, Any]] = []

    async def list_lanes(self, agent_id: str) -> dict[str, Any]:
        assert agent_id == "ark"
        return {"lanes": [{
            "lane_id": "lane-independent", "lane_key": "evolving", "agent_id": agent_id,
            "enabled": True,
        }]}

    async def list_resources(self, agent_id: str) -> dict[str, Any]:
        assert agent_id == "ark"
        return {"resources": [
            {
                "resource_id": "rm-lane-evolving", "resource_type": "rollout_concurrency",
                "scope": "agent", "agent_id": agent_id, "enabled": True,
            },
            {
                "resource_id": "openviking_memory_identities", "resource_type": "memory_identity",
                "scope": "global", "enabled": True, "required_for_task": True,
                "default_amount": 0, "applicable_workflow_ids": ["ark_viking_external_training"],
            },
            {
                "resource_id": "task_manager_executor", "resource_type": "workflow_concurrency",
                "scope": "global", "enabled": True, "required_for_task": True,
                "default_amount": 0, "config": {"automatic_request": {"amount": 1}},
            },
        ]}

    async def create_training_task(self, body: dict[str, Any]) -> dict[str, Any]:
        self.created_bodies.append(body)
        self.created_count += 1
        return {"task_id": f"task-{self.created_count}", "status": "pending"}

    async def get_case(self, case_id: str) -> dict[str, Any]:
        dataset_id = {"case-1": "dataset-1", "case-2": "dataset-2"}[case_id]
        return {
            "case_id": case_id,
            "dataset_id": dataset_id,
            "name": f"adapter {case_id}",
            "envelope": {"user_query": f"hello {case_id}"},
        }

    async def list_rollout_source_cases(
        self,
        task_id: str,
        *,
        phase: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        assert task_id.startswith("task-")
        rows = (
            [
                {
                    "case_id": "101",
                    "input": {"prompt": "viking train prompt"},
                    "expected_answer": "viking train answer",
                    "metadata": {"viking_row_id": 101},
                }
            ]
            if phase == "train"
            else []
        )
        return {
            "phase": phase,
            "page": page,
            "page_size": page_size,
            "total": len(rows),
            "cases": rows,
        }

    async def wait_for_ov_wait(
        self,
        task_id: str,
        *,
        poll_interval_seconds: float,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        assert poll_interval_seconds > 0
        assert timeout_seconds > 0
        return {
            "task_id": task_id, "status": "running", "current_step": "OV_WAIT",
            "params": {"agent_lane_key": "evolving"},
        }

    async def get_training_task(self, task_id: str) -> dict[str, Any]:
        assert task_id in {"task-1", "task-existing"}
        return {"task_id": task_id, "status": "running", "current_step": "OV_WAIT"}

    async def complete_external_training(
        self,
        task_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        assert task_id == "task-1"
        assert idempotency_key.endswith(":task-1:complete")
        self.completed = True
        return {"task_id": task_id, "status": "succeeded"}

    async def submit_rollout_eval(
        self,
        task_id: str,
        *,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        assert task_id == "task-1"
        assert body["case_ids"] == ["case-1"]
        assert idempotency_key
        self.submitted_rollouts.append(body)
        return {
            "batch_rollout_id": "batch-1",
            "case_rollouts": [{"case_id": "case-1", "case_rollout_id": "case-rollout-1"}],
        }

    async def get_case_rollout(self, task_id: str, case_rollout_id: str) -> dict[str, Any]:
        return {
            "status": "completed",
            "result": {
                "final_answer": "done",
                "messages": [
                    {"id": "m1", "role": "user", "content": "hello"},
                    {"id": "m2", "role": "assistant", "content": "done"},
                ],
                "evaluation": {"passed": True, "score": 1.0},
                "evaluator_status": "succeeded",
            },
        }


@pytest.mark.asyncio
async def test_generic_service_contract_executes_platform_rollout() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            lane_key="evolving",
            rollout_resource_id="rm-lane-evolving",
            extra_header={"x-vaka-request-source": "ark-lx"},
            agent_execution={
                "contract_id": "ark.viking-rollout",
                "contract_version": "3",
                "schema_digest": "sha256:test",
                "values": {"memory_openviking_target": "evolving-dutao"},
            },
            rollout_concurrency=2,
            rollout_poll_interval_seconds=0.001,
            rollout_timeout_seconds=1,
            admin_token="admin-secret",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        start_response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "run-1",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 30,
                "casehub": {"dataset_ids": ["dataset-1"], "case_ids": ["case-1"]},
            },
        )
        assert start_response.status_code == 200
        assert start_response.json()["task_id"] == "task-1"
        assert platform_client.created_count == 1
        assert platform_client.created_bodies[0]["casehub_dataset_ids"] == ["dataset-1"]
        assert platform_client.created_bodies[0]["workers"] == 30
        assert platform_client.created_bodies[0]["agent_id"] == "ark"
        assert "agent_lane_id" not in platform_client.created_bodies[0]
        assert "agent_lane_concurrency" not in platform_client.created_bodies[0]
        assert "agent_lane_key" not in platform_client.created_bodies[0]
        assert platform_client.created_bodies[0]["scheduling"]["lane_key"] == "evolving"
        requests = platform_client.created_bodies[0]["scheduling"]["resource_requests"]
        assert {item["resource_id"]: item["amount"] for item in requests} == {
            "rm-lane-evolving": 30, "task_manager_executor": 1,
        }
        assert platform_client.created_bodies[0]["agent_execution"] == {
            "contract_id": "ark.viking-rollout",
            "contract_version": "3",
            "schema_digest": "sha256:test",
            "values": {"memory_openviking_target": "evolving-dutao"},
        }
        assert start_response.json()["concurrency"] == 30
        assert start_response.json()["task_casehub_dataset_ids"] == ["dataset-1"]
        assert platform_client.created_bodies[0]["task_name"].endswith("_run-1")

        duplicate_start = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "run-1",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 30,
                "casehub": {"dataset_ids": ["dataset-1"], "case_ids": ["case-1"]},
            },
        )
        assert duplicate_start.json()["task_id"] == "task-1"
        assert platform_client.created_count == 1

        case_response = await client.post(
            "/v1/cases/query",
            json={
                "dataset": "ark4-0",
                "domain": "ark",
                "split": "train",
                "limit": 10,
                "filters": {"_openviking_benchmark_run_id": "run-1"},
            },
        )
        assert case_response.status_code == 200
        assert len(case_response.json()["cases"]) == 1
        assert case_response.json()["cases"][0]["metadata"]["_ark_rollout_batch"]["case_ids"] == [
            "case-1"
        ]

        execute_response = await client.post(
            "/v1/rollouts/execute",
            json={
                "case": case_to_dict(make_case()),
                "policy_set": policy_set_to_dict(
                    ExperienceSet(
                        root_uri="viking://user/memories/experiences",
                        policies=[],
                    )
                ),
                "execution_context": {
                    "policy_snapshot_id": "snapshot-1",
                    "metadata": {"training": True, "epoch": 0},
                },
                "options": {"_openviking_benchmark_run_id": "run-1"},
            },
        )
        assert execute_response.status_code == 200
        execution_id = execute_response.json()["execution_id"]

        for _ in range(100):
            poll_response = await client.get(f"/v1/rollouts/executions/{execution_id}")
            payload = poll_response.json()
            if payload["status"] == "completed":
                break
            await asyncio.sleep(0.001)
        else:
            raise AssertionError("generic rollout execution did not complete")

        assert payload["rollout"]["evaluation"]["passed"] is True
        assert [message["role"] for message in payload["rollout"]["messages"]] == [
            "user",
            "assistant",
        ]
        assert platform_client.submitted_rollouts[0]["extra_header"] == {
            "x-vaka-request-source": "ark-lx"
        }

        unauthorized = await client.get("/admin/platform-runs")
        assert unauthorized.status_code == 401

        admin_headers = {"X-Ark4-Admin-Token": "admin-secret"}
        task_response = await client.get("/admin/platform-runs/run-1", headers=admin_headers)
        assert task_response.status_code == 200
        assert task_response.json()["task_id"] == "task-1"

        complete_response = await client.post(
            "/v1/runs/run-1/complete",
        )
        assert complete_response.status_code == 200
        assert complete_response.json()["completion"]["status"] == "succeeded"
        assert platform_client.completed is True


@pytest.mark.asyncio
async def test_each_run_has_its_own_casehub_selection() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(dataset="ark4-0", domain="ark"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        for run_id, dataset_id, case_id in (
            ("run-1", "dataset-1", "case-1"),
            ("run-2", "dataset-2", "case-2"),
        ):
            response = await client.post(
                "/v1/runs/start",
                json={
                    "run_id": run_id,
                    "dataset": "ark4-0",
                    "domain": "ark",
                    "casehub": {"dataset_ids": [dataset_id], "case_ids": [case_id]},
                },
            )
            assert response.status_code == 200
            assert response.json()["casehub_case_ids"] == [case_id]
            assert response.json()["case_count"] == 1

            cases = await client.post(
                "/v1/cases/query",
                json={
                    "dataset": "ark4-0",
                    "domain": "ark",
                    "split": "train",
                    "limit": 10,
                    "filters": {"_openviking_benchmark_run_id": run_id},
                },
            )
            assert cases.status_code == 200
            assert [item["input"]["task_id"] for item in cases.json()["cases"]] == [case_id]

    assert [body["casehub_dataset_ids"] for body in platform_client.created_bodies] == [
        ["dataset-1"],
        ["dataset-2"],
    ]


@pytest.mark.asyncio
async def test_viking_external_run_uses_v2_task_body_and_phase_source() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            workflow_id="ark_viking_external_training",
            task_body={
                "schema_version": "training-task-request.v2",
                "name": "viking-external",
                "experiment_id": "exp-external",
                "viking_experiment_sets": [
                    {"experiment_set_id": 371, "version": "V1", "role": "train"},
                    {"experiment_set_id": 372, "version": "V1", "role": "eval"},
                ],
            },
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "viking-run",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 1,
                "casehub": {"dataset_ids": ["viking"], "case_ids": ["101"]},
            },
        )

        assert response.status_code == 200
        assert response.json()["case_count"] == 1
        assert platform_client.created_bodies[0]["name"] == "viking-external_viking-run"
        assert "casehub_dataset_ids" not in platform_client.created_bodies[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", [False, True], ids=["flat", "v2-compat"])
async def test_custom_task_body_routes_and_requests_runner_concurrency(v2: bool) -> None:
    platform_client = CompletedPlatformClient()
    task_body = (
        {"schema_version": "training-task-request.v2", "name": "custom"}
        if v2 else {"task_name": "custom", "workflow_id": "ark_viking_external_training"}
    )
    original = deepcopy(task_body)
    config = ArkAdapterServiceConfig(
        dataset="ark4-0", domain="ark", workflow_id="ark_viking_external_training",
        task_body=task_body, agent_id="ark", lane_key="evolving",
        rollout_resource_id="rm-lane-evolving",
    )
    app = create_app(client=platform_client, config=config)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
    ) as client:
        response = await client.post("/v1/runs/start", json={
            "run_id": "custom-lane", "dataset": "ark4-0", "domain": "ark", "concurrency": 30,
        })
    assert response.status_code == 200, response.text
    body = platform_client.created_bodies[0]
    binding = body["agent"] if v2 else body
    assert binding["agent_id"] == "ark"
    assert "lane_id" not in binding
    assert "agent_lane_id" not in binding
    assert body["scheduling"]["lane_key"] == "evolving"
    assert {item["resource_id"]: item["amount"] for item in body["scheduling"]["resource_requests"]} == {
        "rm-lane-evolving": 30, "openviking_memory_identities": 1, "task_manager_executor": 1,
    }
    assert "agent_lane_key" not in body
    assert "lane_key" not in binding
    assert config.task_body == original  # per-run binding must not mutate the template


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", [False, True], ids=["flat", "v2-compat"])
async def test_custom_task_body_cannot_silently_override_configured_lane(v2: bool) -> None:
    platform_client = CompletedPlatformClient()
    task_body = {"scheduling": {"lane_key": "other-lane"}}
    if v2:
        task_body["schema_version"] = "training-task-request.v2"
    app = create_app(client=platform_client, config=ArkAdapterServiceConfig(
        dataset="ark4-0", domain="ark", workflow_id="ark_viking_external_training",
        task_body=task_body, lane_key="evolving", rollout_resource_id="rm-lane-evolving",
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
    ) as client:
        response = await client.post("/v1/runs/start", json={
            "run_id": "conflicting-lane", "dataset": "ark4-0", "domain": "ark",
        })
    assert response.status_code == 400
    assert "conflicts with training_task.lane_key" in response.json()["detail"]
    assert platform_client.created_bodies == []


@pytest.mark.asyncio
@pytest.mark.parametrize("v2", [False, True], ids=["flat", "v2-compat"])
@pytest.mark.parametrize("configured", [False, True])
async def test_custom_task_body_cannot_mix_lane_and_unified_resources(v2: bool, configured: bool) -> None:
    platform_client = CompletedPlatformClient()
    resources = [{"resource_id": "openviking_memory_identities", "amount": 1}]
    task_body = (
        {"schema_version": "training-task-request.v2", "agent": {"lane_id": "old-lane"}}
        if v2 else {"agent_lane_id": "old-lane"}
    )
    if not configured:
        task_body["scheduling"] = {"resource_requests": resources}
    app = create_app(client=platform_client, config=ArkAdapterServiceConfig(
        dataset="ark4-0", domain="ark", workflow_id="ark_viking_external_training",
        task_body=task_body, lane_key="evolving" if configured else "",
        rollout_resource_id="rm-lane-evolving" if configured else "",
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
    ) as client:
        response = await client.post("/v1/runs/start", json={
            "run_id": "mixed-resource-modes", "dataset": "ark4-0", "domain": "ark",
        })
    assert response.status_code == 400
    assert "cannot be combined with unified resource requests" in response.json()["detail"]
    assert platform_client.created_bodies == []


@pytest.mark.asyncio
async def test_custom_task_scheduling_preserves_template_priority_and_extra_requests() -> None:
    platform_client = CompletedPlatformClient()
    task_body = {
        "schema_version": "training-task-request.v2",
        "scheduling": {
            "priority": 42, "depends_on_task_ids": ["task-upstream"],
            "resource_requests": [{"resource_id": "extra-resource", "amount": 2}],
        },
    }
    original = deepcopy(task_body)
    app = create_app(client=platform_client, config=ArkAdapterServiceConfig(
        dataset="ark4-0", domain="ark", workflow_id="ark_viking_external_training",
        task_body=task_body, lane_key="evolving", rollout_resource_id="rm-lane-evolving",
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
    ) as client:
        response = await client.post("/v1/runs/start", json={
            "run_id": "custom-scheduling", "dataset": "ark4-0", "domain": "ark", "concurrency": 30,
        })
    assert response.status_code == 200, response.text
    scheduling = platform_client.created_bodies[0]["scheduling"]
    assert scheduling["priority"] == 42
    assert scheduling["depends_on_task_ids"] == ["task-upstream"]
    assert scheduling["lane_key"] == "evolving"
    assert {item["resource_id"]: item["amount"] for item in scheduling["resource_requests"]} == {
        "extra-resource": 2, "rm-lane-evolving": 30, "openviking_memory_identities": 1,
        "task_manager_executor": 1,
    }
    assert task_body == original


@pytest.mark.asyncio
@pytest.mark.parametrize("actual_lane", [None, "main"])
async def test_task_with_wrong_frozen_lane_cannot_start_rollouts(actual_lane) -> None:
    class WrongLaneClient(CompletedPlatformClient):
        async def wait_for_ov_wait(self, task_id, **kwargs):
            return {
                "task_id": task_id, "status": "running", "current_step": "VIKING_OV_WAIT",
                "params": {"agent_lane_key": actual_lane},
            }

    platform_client = WrongLaneClient()
    app = create_app(client=platform_client, config=ArkAdapterServiceConfig(
        dataset="ark4-0", domain="ark", workflow_id="ark_viking_external_training",
        task_body={"schema_version": "training-task-request.v2"},
        lane_key="evolving", rollout_resource_id="rm-lane-evolving",
    ))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
    ) as client:
        body = {"run_id": "wrong-lane", "dataset": "ark4-0", "domain": "ark", "concurrency": 1}
        response = await client.post("/v1/runs/start", json=body)
        assert response.status_code == 502
        assert "task-1" in response.json()["detail"]
        assert "expected 'evolving'; no rollout submitted" in response.json()["detail"]
        assert (await client.post("/v1/runs/start", json=body)).status_code == 502
    assert app.state.run_registry.get("wrong-lane").status == "routing_mismatch"
    assert platform_client.created_count == 1
    assert platform_client.submitted_rollouts == []


@pytest.mark.asyncio
async def test_viking_external_run_can_attach_existing_task() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            workflow_id="ark_viking_external_training",
            existing_task_id="task-existing",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "attached-run",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 1,
            },
        )

    assert response.status_code == 200
    assert response.json()["task_id"] == "task-existing"
    assert platform_client.created_bodies == []


@pytest.mark.asyncio
async def test_compact_viking_resolves_per_run_and_receives_runner_plan(monkeypatch) -> None:
    platform_client = CompletedPlatformClient()
    resolved: list[dict[str, Any]] = []

    async def build(client, **kwargs):
        assert client is platform_client
        resolved.append(kwargs)
        return {
            "schema_version": "training-task-request.v2",
            "name": kwargs["name"],
            "agent": {
                "execution": {"contract_version": str(len(resolved))},
                "agent_id": kwargs["agent_id"],
            },
            "scheduling": {
                "lane_key": kwargs["lane_key"],
                "resource_requests": [{
                    "resource_id": kwargs["rollout_resource_id"], "amount": kwargs["concurrency"],
                }],
            },
        }

    monkeypatch.setattr("service_app.build_viking_task_request", build)
    app = create_app(
        client=platform_client,
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            workflow_id="ark_viking_external_training",
            viking_experiment_sets=[
                {"experiment_set_id": 371, "version": "V1", "role": "train"},
                {"experiment_set_id": 372, "version": "V1", "role": "eval"},
            ],
            lane_key="evolving",
            rollout_resource_id="rm-lane-evolving",
            request_source="agentmemory",
            runtime_params={"task_timeout": 4800},
            rollout_concurrency=1,
        ),
    )
    body = {
        "run_id": "auto-run",
        "dataset": "ark4-0",
        "domain": "ark",
        "concurrency": 30,
        "training_plan": {"train_epochs": 5, "train_trials": 1, "eval_trials": 10},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        response = await client.post("/v1/runs/start", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["resolved_task_request"] == platform_client.created_bodies[0]
        assert response.json()["training_plan"] == body["training_plan"]
        assert resolved[0]["training_plan"] == body["training_plan"]
        assert resolved[0]["concurrency"] == 30
        assert resolved[0]["case_timeout_seconds"] == 4800
        assert resolved[0]["request_source"] == "agentmemory"
        assert resolved[0]["agent_id"] == "ark"
        assert resolved[0]["lane_key"] == "evolving"
        assert resolved[0]["rollout_resource_id"] == "rm-lane-evolving"
        assert "backend" not in resolved[0]
        assert "agent_lane_id" not in resolved[0]
        eval_response = await client.post("/v1/runs/start", json=body)
        assert eval_response.status_code == 200
        assert eval_response.json()["training_plan"] == body["training_plan"]
        assert len(resolved) == 1
        body["training_plan"]["train_epochs"] = 0
        assert (await client.post("/v1/runs/start", json=body)).status_code == 400
        body["run_id"] = "auto-run-2"
        eval_response = await client.post("/v1/runs/start", json=body)
        assert eval_response.status_code == 200
        assert eval_response.json()["training_plan"]["train_epochs"] == 0
        assert len(resolved) == 2
        assert resolved[1]["training_plan"]["train_epochs"] == 0
        body["run_id"] = "old-runner"
        del body["training_plan"]
        response = await client.post("/v1/runs/start", json=body)
        assert response.status_code == 400
        assert "training_plan" in response.text
        assert platform_client.created_count == 2


@pytest.mark.parametrize("field", ["agent_lane_id", "agent_lane_key"])
def test_service_config_does_not_accept_legacy_lane_fields(field: str) -> None:
    with pytest.raises(TypeError, match=field):
        ArkAdapterServiceConfig(dataset="ark4-0", domain="ark", **{field: "evolving"})


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["lane_key", "rollout_resource_id"])
@pytest.mark.parametrize("value", [None, "", " "])
async def test_compact_viking_without_scheduling_choice_never_creates_task(field, value) -> None:
    platform_client = CompletedPlatformClient()
    choices = {"lane_key": "evolving", "rollout_resource_id": "rm-lane-evolving", field: value}
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            workflow_id="ark_viking_external_training",
            viking_experiment_sets=[
                {"experiment_set_id": 371, "version": "V1", "role": "train"},
                {"experiment_set_id": 372, "version": "V1", "role": "eval"},
            ],
            request_source="agentmemory",
            **choices,
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://adapter.test"
    ) as client:
        response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "missing-scheduling-choice",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 30,
                "training_plan": {"train_epochs": 5, "train_trials": 1, "eval_trials": 10},
            },
        )

    assert response.status_code == 400
    assert f"training_task.{field} is required" in response.json()["detail"]
    assert platform_client.created_count == 0
    assert platform_client.created_bodies == []


@pytest.mark.asyncio
async def test_case_query_records_exact_requested_batch_page() -> None:
    class ThreeCasePlatformClient(CompletedPlatformClient):
        async def list_rollout_source_cases(
            self,
            task_id: str,
            *,
            phase: str,
            page: int,
            page_size: int,
        ) -> dict[str, Any]:
            rows = (
                [
                    {
                        "case_id": str(row_id),
                        "input": {"prompt": f"prompt {row_id}"},
                        "expected_answer": f"answer {row_id}",
                        "metadata": {"viking_row_id": row_id},
                    }
                    for row_id in (101, 102, 103)
                ]
                if phase == "train"
                else []
            )
            start = max(0, page - 1) * page_size
            selected = rows[start : start + page_size]
            return {
                "phase": phase,
                "page": page,
                "page_size": page_size,
                "total": len(rows),
                "cases": selected,
            }

    platform_client = ThreeCasePlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(
            dataset="ark4-0",
            domain="ark",
            workflow_id="ark_viking_external_training",
            existing_task_id="task-existing",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        started = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "attached-run",
                "dataset": "ark4-0",
                "domain": "ark",
                "concurrency": 2,
            },
        )
        assert started.status_code == 200
        response = await client.post(
            "/v1/cases/query",
            json={
                "dataset": "ark4-0",
                "domain": "ark",
                "split": "train",
                "limit": 2,
                "filters": {"_openviking_benchmark_run_id": "attached-run"},
            },
        )

    assert response.status_code == 200
    cases = response.json()["cases"]
    assert len(cases) == 2
    descriptors = [case["metadata"]["_ark_rollout_batch"] for case in cases]
    assert descriptors[0] == descriptors[1]
    assert descriptors[0]["case_ids"] == ["101", "102"]


@pytest.mark.asyncio
async def test_casehub_selection_is_validated_before_task_creation() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(dataset="ark4-0", domain="ark"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "run-invalid",
                "dataset": "ark4-0",
                "domain": "ark",
                "casehub": {"dataset_ids": ["dataset-1"], "case_ids": ["case-2"]},
            },
        )

    assert response.status_code == 400
    assert "do not belong" in response.json()["detail"]
    assert platform_client.created_count == 0


@pytest.mark.asyncio
async def test_case_query_can_select_one_dataset_from_multi_dataset_run() -> None:
    platform_client = CompletedPlatformClient()
    app = create_app(
        client=platform_client,  # type: ignore[arg-type]
        config=ArkAdapterServiceConfig(dataset="ark4-0", domain="ark"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://adapter.test",
    ) as client:
        start_response = await client.post(
            "/v1/runs/start",
            json={
                "run_id": "run-multi",
                "dataset": "ark4-0",
                "domain": "ark",
                "casehub": {
                    "dataset_ids": ["dataset-1", "dataset-2"],
                    "case_ids": ["case-1", "case-2"],
                    "task_dataset_ids": ["dataset-1"],
                },
            },
        )
        assert start_response.status_code == 200
        assert platform_client.created_bodies[0]["casehub_dataset_ids"] == ["dataset-1"]
        assert start_response.json()["task_casehub_dataset_ids"] == ["dataset-1"]

        cases = await client.post(
            "/v1/cases/query",
            json={
                "dataset": "ark4-0",
                "domain": "ark",
                "split": "train",
                "limit": 10,
                "filters": {
                    "_openviking_benchmark_run_id": "run-multi",
                    "_openviking_casehub_dataset_ids": ["dataset-2"],
                },
            },
        )

    assert cases.status_code == 200
    assert [item["input"]["task_id"] for item in cases.json()["cases"]] == ["case-2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("run_concurrency", [2, 30])
async def test_viking_rollout_post_uses_runner_concurrency_not_service_default(
    run_concurrency: int,
) -> None:
    import json

    from platform_client import PlatformClientConfig, TrainingPlatformClient

    submitted_bodies: list[dict[str, Any]] = []
    case_ids = [str(index) for index in range(1, 32)]

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        assert "x-tt-backend" not in request.headers
        assert request.headers["x-vaka-request-source"] == "ark-lx"
        path = request.url.path
        task_path = "/inspect/training/tasks/task-existing"
        if request.method == "GET" and path == task_path:
            return httpx.Response(200, json={"data": {
                "task_id": "task-existing", "status": "running", "current_step": "OV_WAIT",
            }})
        if request.method == "GET" and path.endswith("/rollout-source-cases"):
            rows = [{
                "case_id": case_id,
                "input": {"prompt": f"question {case_id}"},
                "metadata": {"viking_row_id": int(case_id)},
            } for case_id in case_ids] if request.url.params["phase"] == "train" else []
            return httpx.Response(200, json={"data": {
                "cases": rows, "total": len(rows), "page": 1,
                "page_size": int(request.url.params["page_size"]),
            }})
        if request.method == "POST" and path.endswith("/rollout-eval"):
            body = json.loads(request.content)
            submitted_bodies.append(body)
            return httpx.Response(200, json={"data": {
                "batch_rollout_id": "batch-test",
                "case_rollouts": [{
                    "case_id": case_id, "case_rollout_id": f"cr-{case_id}",
                } for case_id in body["case_ids"]],
            }})
        if request.method == "GET" and "/rollout-eval/cr-" in path:
            return httpx.Response(200, json={"data": {
                "status": "completed",
                "result": {
                    "final_answer": "done",
                    "messages": [{"role": "assistant", "content": "done"}],
                    "evaluation": {"passed": True, "score": 1.0},
                    "evaluator_status": "succeeded",
                },
            }})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async with TrainingPlatformClient(
        PlatformClientConfig(
            gateway_base_url="https://platform.test", vaka_request_source="ark-lx"
        ),
        transport=httpx.MockTransport(gateway_handler),
    ) as platform_client:
        app = create_app(
            client=platform_client,
            config=ArkAdapterServiceConfig(
                dataset="ark4-0", domain="ark",
                workflow_id="ark_viking_external_training", existing_task_id="task-existing",
                rollout_concurrency=1, rollout_poll_interval_seconds=0.001,
                extra_header={"x-vaka-request-source": "ark-lx"},
            ),
        )
        assert app.state.rollout_semaphore is None
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://adapter.test",
        ) as client:
            started = await client.post("/v1/runs/start", json={
                "run_id": "concurrency-run", "dataset": "ark4-0", "domain": "ark",
                "concurrency": run_concurrency,
            })
            assert started.status_code == 200
            queried = await client.post("/v1/cases/query", json={
                "dataset": "ark4-0", "domain": "ark", "split": "train", "limit": 31,
                "filters": {"_openviking_benchmark_run_id": "concurrency-run"},
            })
            assert queried.status_code == 200
            executed = await client.post("/v1/rollouts/execute", json={
                "case": queried.json()["cases"][0],
                "policy_set": policy_set_to_dict(ExperienceSet(
                    root_uri="viking://user/memories/experiences", policies=[],
                )),
                "execution_context": {"policy_snapshot_id": "snapshot-test", "metadata": {}},
                "options": {"_openviking_benchmark_run_id": "concurrency-run"},
            })
            assert executed.status_code == 200
            execution_id = executed.json()["execution_id"]
            for _ in range(100):
                response = await client.get(f"/v1/rollouts/executions/{execution_id}")
                if response.json()["status"] == "completed":
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("rollout did not complete")

    assert len(submitted_bodies) == 1
    assert submitted_bodies[0]["workers"] == run_concurrency
    assert submitted_bodies[0]["case_ids"] == case_ids
    assert submitted_bodies[0]["extra_header"] == {"x-vaka-request-source": "ark-lx"}
