from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx
import pytest
from platform_client import PlatformAPIError, PlatformClientConfig, TrainingPlatformClient
from task_request import VIKING_WORKFLOW, build_viking_task_request, validate_sets


@pytest.mark.asyncio
async def test_metadata_connection_failure_is_clear_and_never_creates_task(choices) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        raise httpx.ConnectError("unavailable", request=request)

    async with TrainingPlatformClient(
        PlatformClientConfig(gateway_base_url="https://platform.test"),
        transport=httpx.MockTransport(fail),
    ) as client:
        with pytest.raises(
            PlatformAPIError, match="could not query platform metadata.*ConnectError"
        ):
            await build_viking_task_request(client, **choices)


@pytest.mark.asyncio
async def test_required_other_lane_fails_instead_of_requesting_two_lanes(metadata, choices) -> None:
    other = deepcopy(metadata["resources"]["resources"][0])
    other.update(resource_id="other-lane", required_for_task=True, default_amount=2)
    other["config"]["lane_key"] = "main"
    metadata["resources"]["resources"].append(other)
    with pytest.raises(PlatformAPIError, match="conflicts with selected lane"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["schema", "properties", "version"])
async def test_null_contract_metadata_has_clear_error(metadata, choices, location) -> None:
    contract = metadata["contract"]["contract"]
    if location == "schema":
        contract["json_schema"] = None
    elif location == "properties":
        contract["json_schema"]["properties"] = None
    else:
        contract["json_schema"]["properties"]["openviking_version"] = None
    with pytest.raises(PlatformAPIError, match="no default"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_null_optional_resource_config_is_supported(metadata, choices) -> None:
    lane = metadata["resources"]["resources"][0]
    lane["lane_key"] = "agentmemory"
    lane["config"] = None
    executor = metadata["resources"]["resources"][2]
    executor["config"]["automatic_request"] = None
    executor["default_amount"] = 1
    assert (await resolve(metadata, choices))["scheduling"]["resource_requests"][0]["amount"] == 30


@pytest.fixture
def metadata() -> dict[str, Any]:
    return {
        "contract": {
            "supported": True,
            "contract": {
                "contract_id": "ark.viking-rollout",
                "contract_version": "current-contract-version",
                "schema_digest": "sha256:current-contract",
                "json_schema": {
                    "properties": {
                        "model_ep": {"default": "default", "enum": ["default", "custom"]},
                        "openviking_version": {
                            "default": "v-platform-default",
                            "enum": ["v-first-but-not-default", "v-platform-default"],
                        },
                        "request_source": {"type": "string"},
                    },
                    "required": ["model_ep", "openviking_version", "request_source"],
                },
            },
        },
        "experiments": {
            "experiments": [
                {
                    "experiment_id": "exp-current",
                    "agent_id": "ark",
                    "workflow_id": VIKING_WORKFLOW,
                    "component_type": "memory",
                    "status": "active",
                }
            ]
        },
        "targets": {"targets": [{"component_type": "memory", "target": "openviking_experiences"}]},
        "resources": {
            "resources": [
                {
                    "resource_id": "rm-current-agentmemory-lane",
                    "resource_type": "rollout_concurrency",
                    "agent_id": "ark",
                    "scope": "agent",
                    "lane_key": None,
                    "config": {"lane_key": "agentmemory"},
                    "enabled": True,
                },
                {
                    "resource_id": "openviking_memory_identities",
                    "resource_type": "memory_identity",
                    "required_for_task": True,
                    "default_amount": 0,
                },
                {
                    "resource_id": "task_manager_executor",
                    "resource_type": "executor",
                    "required_for_task": True,
                    "default_amount": 0,
                    "config": {"automatic_request": {"amount": 1}},
                },
            ]
        },
    }


@pytest.fixture
def choices() -> dict[str, Any]:
    return {
        "name": "test-run-id",
        "lane_key": "agentmemory",
        "experiment_sets": [
            {"experiment_set_id": 371, "version": "V1", "role": "train"},
            {"experiment_set_id": 372, "version": "V2", "role": "eval"},
        ],
        "concurrency": 30,
        "training_plan": {"train_epochs": 5, "train_trials": 1, "eval_trials": 10},
        "request_source": "agentmemory",
        "case_timeout_seconds": 4800,
    }


async def resolve(metadata: dict[str, Any], choices: dict[str, Any]) -> dict[str, Any]:
    """Exercise real metadata client methods without any network or Task POST."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET", "metadata resolution must never write platform state"
        assert request.headers["X-API-Key"] == "test-key"
        assert request.headers["X-Project-Id"] == "test-project"
        assert request.headers["x-vaka-request-source"] == "agentmemory"
        path = request.url.path
        if path == "/inspect/training/agents/ark/execution-contract":
            response = metadata["contract"]
        elif path == "/inspect/training/agents/ark/experiments":
            response = metadata["experiments"]
        elif path == "/inspect/resources/resources":
            assert dict(request.url.params) == {
                "applicable_agent_id": "ark",
                "include_disabled": "false",
            }
            response = metadata["resources"]
        elif path.startswith("/inspect/training/agents/ark/experiments/") and path.endswith(
            "/targets"
        ):
            response = metadata["targets"]
        else:
            raise AssertionError(f"Unexpected metadata request: {request.url}")
        return httpx.Response(200, json={"data": response})

    async with TrainingPlatformClient(
        PlatformClientConfig(
            gateway_base_url="https://platform.test",
            api_key="test-key",
            project_id="test-project",
            vaka_request_source="agentmemory",
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await build_viking_task_request(client, **choices)
    assert len(requests) == 4
    return result


@pytest.mark.asyncio
async def test_resolves_current_metadata_and_runner_settings(metadata, choices) -> None:
    original_metadata, original_choices = deepcopy(metadata), deepcopy(choices)
    body = await resolve(metadata, choices)

    assert body["schema_version"] == "training-task-request.v2"
    assert body["name"] == "test-run-id"
    assert body["agent"] == {
        "agent_id": "ark",
        "execution": {
            "contract_id": "ark.viking-rollout",
            "contract_version": "current-contract-version",
            "schema_digest": "sha256:current-contract",
            "values": {
                "model_ep": "default",
                "openviking_version": "v-platform-default",
                "request_source": "agentmemory",
            },
        },
    }
    assert body["experiment_id"] == "exp-current"
    assert body["target"] == {"component_type": "memory", "target": "openviking_experiences"}
    assert body["evaluator"] == {"evaluator_id": "viking_experiment_suite@v1", "workers": 30}
    assert body["execution"] == {"rollout_workers": 30, "case_timeout_seconds": 4800}
    assert body["memory"] == {"train_epochs": 1, "train_trials": 1, "eval_trials": 1}
    assert body["viking_experiment_sets"] == choices["experiment_sets"]
    assert body["scheduling"]["resource_requests"] == [
        {"resource_id": "rm-current-agentmemory-lane", "amount": 30},
        {"resource_id": "openviking_memory_identities", "amount": 1},
        {"resource_id": "task_manager_executor", "amount": 1},
    ]
    assert metadata == original_metadata
    assert choices == original_choices
    body["viking_experiment_sets"][0]["version"] = "V-mutated"
    assert choices["experiment_sets"][0]["version"] == "V1"


@pytest.mark.asyncio
async def test_metadata_changes_are_resolved_for_each_run(metadata, choices) -> None:
    before = await resolve(metadata, choices)
    contract = metadata["contract"]["contract"]
    contract["contract_version"] = "new-contract-version"
    contract["schema_digest"] = "sha256:new-contract"
    contract["json_schema"]["properties"]["openviking_version"]["default"] = (
        "v-first-but-not-default"
    )
    after = await resolve(metadata, choices)

    assert before["agent"]["execution"]["contract_version"] == "current-contract-version"
    assert after["agent"]["execution"]["contract_version"] == "new-contract-version"
    assert after["agent"]["execution"]["schema_digest"] == "sha256:new-contract"
    assert after["agent"]["execution"]["values"]["openviking_version"] == "v-first-but-not-default"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["model_ep", "openviking_version"])
async def test_missing_platform_default_fails_instead_of_guessing(metadata, choices, field) -> None:
    metadata["contract"]["contract"]["json_schema"]["properties"][field].pop("default")
    with pytest.raises(PlatformAPIError, match=f"no default for {field}"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["contract_id", "contract_version", "schema_digest"])
async def test_incomplete_execution_contract_fails_closed(metadata, choices, field) -> None:
    metadata["contract"]["contract"].pop(field)
    with pytest.raises(PlatformAPIError, match=f"no {field}"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_unavailable_execution_contract_fails_closed(metadata, choices) -> None:
    metadata["contract"]["supported"] = False
    with pytest.raises(PlatformAPIError, match="contract is unavailable"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_new_required_contract_field_fails_closed(metadata, choices) -> None:
    metadata["contract"]["contract"]["json_schema"]["required"].append("new_required_field")
    with pytest.raises(PlatformAPIError, match="unsupported fields.*new_required_field"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_unsupported_source_contract_fails_closed(metadata, choices) -> None:
    metadata["contract"]["contract"]["json_schema"]["properties"].pop("request_source")
    with pytest.raises(PlatformAPIError, match="does not support request_source"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_invalid_default_not_in_contract_enum_fails_closed(metadata, choices) -> None:
    metadata["contract"]["contract"]["json_schema"]["properties"]["openviking_version"][
        "default"
    ] = "v-not-allowed"
    with pytest.raises(PlatformAPIError, match="not in its allowed enum"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_explicit_model_endpoint_overrides_only_model_default(metadata, choices) -> None:
    choices["model_ep"] = "custom"
    body = await resolve(metadata, choices)
    assert body["agent"]["execution"]["values"] == {
        "model_ep": "custom",
        "openviking_version": "v-platform-default",
        "request_source": "agentmemory",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [0, 2])
@pytest.mark.parametrize("kind", ["experiments", "targets"])
async def test_experiment_and_target_must_be_unique(metadata, choices, kind, row_count) -> None:
    metadata[kind][kind] *= row_count
    with pytest.raises(PlatformAPIError, match=f"expected exactly one.*found {row_count}"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_explicit_experiment_resolves_ambiguity(metadata, choices) -> None:
    duplicate = deepcopy(metadata["experiments"]["experiments"][0])
    duplicate["experiment_id"] = "exp-second"
    metadata["experiments"]["experiments"].append(duplicate)
    choices["experiment_id"] = "exp-second"
    body = await resolve(metadata, choices)
    assert body["experiment_id"] == "exp-second"


@pytest.mark.asyncio
async def test_missing_explicit_experiment_is_not_replaced_by_default(metadata, choices) -> None:
    choices["experiment_id"] = "exp-missing"
    with pytest.raises(PlatformAPIError, match="found 0"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_experiments_ignore_other_agents_workflows_components_and_inactive_entries(
    metadata, choices
) -> None:
    template = metadata["experiments"]["experiments"][0]
    for overrides in [
        {"agent_id": "other-agent"},
        {"workflow_id": "some-other-workflow"},
        {"component_type": "prompt"},
        {"status": "disabled"},
    ]:
        metadata["experiments"]["experiments"].append(
            {**template, "experiment_id": "exp-inapplicable", **overrides}
        )
    metadata["targets"]["targets"].append({"component_type": "prompt", "target": "prompt"})
    body = await resolve(metadata, choices)
    assert body["experiment_id"] == "exp-current"
    assert body["target"]["target"] == "openviking_experiences"


@pytest.mark.asyncio
async def test_agent_scoped_experiment_response_need_not_repeat_agent_id(metadata, choices) -> None:
    metadata["experiments"]["experiments"][0].pop("agent_id")
    body = await resolve(metadata, choices)
    assert body["experiment_id"] == "exp-current"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["agent", "lane"])
async def test_lane_supports_both_resource_metadata_formats(metadata, choices, scope) -> None:
    lane = metadata["resources"]["resources"][0]
    lane["scope"] = scope
    if scope == "lane":
        lane["lane_key"] = lane["config"].pop("lane_key")
    body = await resolve(metadata, choices)
    assert body["scheduling"]["resource_requests"][0] == {
        "resource_id": "rm-current-agentmemory-lane",
        "amount": 30,
    }


@pytest.mark.asyncio
async def test_resources_ignore_other_agents_workflows_disabled_and_optional_entries(
    metadata, choices
) -> None:
    resources = metadata["resources"]["resources"]
    lane = resources[0]
    for overrides in [
        {"agent_id": "other-agent"},
        {"applicable_agent_ids": ["other-agent"]},
        {"applicable_workflow_ids": ["other-workflow"]},
        {"enabled": False},
    ]:
        resources.append({**lane, "resource_id": "inapplicable-lane", **overrides})
        resources.append(
            {
                "resource_id": "inapplicable-required-resource",
                "resource_type": "executor",
                "required_for_task": True,
                "default_amount": 99,
                **overrides,
            }
        )
    resources.append(
        {
            "resource_id": "optional-resource",
            "required_for_task": False,
            "default_amount": 99,
        }
    )
    body = await resolve(metadata, choices)
    assert [row["resource_id"] for row in body["scheduling"]["resource_requests"]] == [
        "rm-current-agentmemory-lane",
        "openviking_memory_identities",
        "task_manager_executor",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("row_count", [0, 2])
async def test_lane_resolution_never_guesses(metadata, choices, row_count) -> None:
    resources = metadata["resources"]["resources"]
    resources[:] = [deepcopy(resources[0]) for _ in range(row_count)] + resources[1:]
    with pytest.raises(PlatformAPIError, match=f"Ark rollout resource.*found {row_count}"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
async def test_required_resources_use_declared_amounts_before_fallbacks(metadata, choices) -> None:
    resources = metadata["resources"]["resources"]
    resources[1]["default_amount"] = 2
    resources[2]["default_amount"] = 3
    body = await resolve(metadata, choices)
    assert body["scheduling"]["resource_requests"][1:] == [
        {"resource_id": "openviking_memory_identities", "amount": 2},
        {"resource_id": "task_manager_executor", "amount": 3},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [None, 0, -1, "1", True, 1.5])
async def test_required_unknown_resource_without_safe_amount_fails_closed(
    metadata, choices, amount
) -> None:
    metadata["resources"]["resources"].append(
        {
            "resource_id": "new-required-resource",
            "resource_type": "some-new-resource",
            "required_for_task": True,
            "default_amount": amount,
        }
    )
    with pytest.raises(PlatformAPIError, match="new-required-resource has no safe amount"):
        await resolve(metadata, choices)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        {"train_epochs": 0, "train_trials": 1, "eval_trials": 1},
        {"train_epochs": 0, "train_trials": 2, "eval_trials": 10},
        {"train_epochs": 5, "train_trials": 1, "eval_trials": 1},
        {"train_epochs": 5, "train_trials": 2, "eval_trials": 10},
    ],
    ids=["eval-only", "repeated-eval-only", "five-epochs", "repeated-train-and-eval"],
)
async def test_platform_counts_are_fixed_without_changing_runner_plan(
    metadata, choices, plan
) -> None:
    choices["training_plan"] = plan
    original_choices = deepcopy(choices)
    body = await resolve(metadata, choices)
    assert body["memory"] == {"train_epochs": 1, "train_trials": 1, "eval_trials": 1}
    assert choices == original_choices
    assert choices["training_plan"] is plan


@pytest.mark.asyncio
@pytest.mark.parametrize("train_epochs", [0, 5], ids=["eval-only", "five-train-epochs"])
async def test_generated_task_passes_platform_single_pass_validation(
    metadata, choices, train_epochs
) -> None:
    choices["training_plan"] = {
        "train_epochs": train_epochs,
        "train_trials": 2,
        "eval_trials": 10,
    }
    original_plan = deepcopy(choices["training_plan"])
    body = await resolve(metadata, choices)
    submitted_bodies: list[dict[str, Any]] = []
    platform_counts = {"train_epochs": 1, "train_trials": 1, "eval_trials": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/inspect/training/tasks"
        submitted = json.loads(request.content)
        submitted_bodies.append(submitted)
        if submitted.get("memory") != platform_counts:
            return httpx.Response(
                422,
                json={
                    "detail": [
                        {
                            "loc": ["body", "memory", field],
                            "msg": "Input should be equal to 1",
                            "input": submitted.get("memory", {}).get(field),
                        }
                        for field in platform_counts
                        if submitted.get("memory", {}).get(field) != 1
                    ]
                },
            )
        return httpx.Response(200, json={"data": {"task_id": "task-eval-only-regression"}})

    async with TrainingPlatformClient(
        PlatformClientConfig(gateway_base_url="https://platform.test"),
        transport=httpx.MockTransport(handler),
    ) as client:
        # Reproduce the old request's 422 before checking the fixed wire payload.
        old_body = {**body, "memory": original_plan}
        with pytest.raises(PlatformAPIError, match="HTTP 422") as error:
            await client.create_training_task(old_body)
        assert error.value.status_code == 422
        created = await client.create_training_task(body)

    assert created["task_id"] == "task-eval-only-regression"
    assert submitted_bodies[0]["memory"] == original_plan
    assert submitted_bodies[1]["memory"] == platform_counts
    assert choices["training_plan"] == original_plan


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"train_epochs": 5, "train_trials": 1},
        {"train_epochs": -1, "train_trials": 1, "eval_trials": 10},
        {"train_epochs": 5, "train_trials": 0, "eval_trials": 10},
        {"train_epochs": 5, "train_trials": 1, "eval_trials": 0},
        {"train_epochs": True, "train_trials": 1, "eval_trials": 10},
    ],
)
async def test_invalid_or_missing_runner_plan_is_not_replaced_with_config_default(
    metadata, choices, plan
) -> None:
    choices["training_plan"] = plan
    with pytest.raises(ValueError, match="runner must provide"):
        await resolve(metadata, choices)


@pytest.mark.parametrize(
    "sets",
    [
        [],
        [{"experiment_set_id": 371, "role": "train"}],
        [{"experiment_set_id": 371, "version": "", "role": "train"}],
        [{"experiment_set_id": 371, "version": "V1", "role": "train", "latest": True}],
        [{"experiment_set_id": True, "version": "V1", "role": "train"}],
        [{"experiment_set_id": 0, "version": "V1", "role": "train"}],
        [{"experiment_set_id": 371, "version": "V1", "role": "validation"}],
        [{"experiment_set_id": 371, "version": "V1", "role": "train"}],
        [{"experiment_set_id": 371, "version": "V1", "role": "train"}] * 2,
    ],
)
def test_set_choice_requires_explicit_version_unique_entries_and_both_roles(sets) -> None:
    with pytest.raises(ValueError):
        validate_sets(sets)
