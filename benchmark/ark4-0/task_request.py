"""Resolve platform metadata per run; keep only experiment choices in config."""

from __future__ import annotations

import asyncio
import math
from copy import deepcopy
from typing import Any

from platform_client import PlatformAPIError, TrainingPlatformClient

VIKING_WORKFLOW = "ark_viking_external_training"


def validate_sets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("training_task.viking_experiment_sets must be a non-empty list")
    seen: set[tuple[int, str, str]] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"experiment_set_id", "version", "role"}:
            raise ValueError("each Viking set needs only experiment_set_id, version and role")
        if type(item["experiment_set_id"]) is not int or item["experiment_set_id"] <= 0:
            raise ValueError("experiment_set_id must be a positive integer")
        if not isinstance(item["version"], str) or not item["version"].strip():
            raise ValueError("Viking set version must be explicitly selected")
        if not isinstance(item["role"], str) or item["role"] not in {"train", "eval"}:
            raise ValueError("Viking set role must be train or eval")
        key = (item["experiment_set_id"], item["version"], item["role"])
        if key in seen:
            raise ValueError("duplicate Viking experiment set")
        seen.add(key)
    if {item["role"] for item in value} != {"train", "eval"}:
        raise ValueError("Viking sets must include train and eval roles")
    return deepcopy(value)


async def build_viking_task_request(
    client: TrainingPlatformClient,
    *,
    name: str,
    agent_id: str,
    lane_key: str,
    rollout_resource_id: str,
    experiment_sets: list[dict[str, Any]],
    concurrency: int,
    training_plan: dict[str, int],
    request_source: str,
    case_timeout_seconds: float,
    model_ep: str = "",
    experiment_id: str = "",
) -> dict[str, Any]:
    """Read metadata only. Never create a Task or allocate a resource here."""
    sets = validate_sets(experiment_sets)
    for key, value in (
        ("agent_id", agent_id),
        ("lane_key", lane_key),
        ("rollout_resource_id", rollout_resource_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"training_task.{key} is required")
    agent_id = agent_id.strip()
    if (
        type(concurrency) is not int
        or concurrency < 1
        or type(case_timeout_seconds) not in {int, float}
        or not math.isfinite(case_timeout_seconds)
        or case_timeout_seconds <= 0
    ):
        raise ValueError("concurrency and case timeout must be positive")
    if not request_source.strip():
        raise ValueError("platform.vaka_request_source is required")
    if set(training_plan) != {"train_epochs", "train_trials", "eval_trials"} or any(
        type(v) is not int or v < (0 if k == "train_epochs" else 1)
        for k, v in training_plan.items()
    ):
        raise ValueError("runner must provide train_epochs, train_trials and eval_trials")
    contract_data, experiment_data, scheduling = await asyncio.gather(
        client.get_execution_contract(agent_id),
        client.list_experiments(agent_id),
        build_task_scheduling(
            client,
            agent_id=agent_id,
            lane_key=lane_key,
            rollout_resource_id=rollout_resource_id,
            concurrency=concurrency,
        ),
    )
    contract = contract_data.get("contract")
    if not contract_data.get("supported") or not isinstance(contract, dict):
        raise PlatformAPIError("Ark execution contract is unavailable")
    execution = {}
    for key in ("contract_id", "contract_version", "schema_digest"):
        if not contract.get(key):
            raise PlatformAPIError(f"execution contract has no {key}")
        execution[key] = contract[key]
    schema = _object(contract.get("json_schema"), "contract.json_schema")
    properties = _object(schema.get("properties"), "contract.json_schema.properties")
    values = {}
    # Use declared defaults, never guess the newest enum value or a local version.
    for key in ("model_ep", "openviking_version"):
        field = _object(properties.get(key), f"contract property {key}")
        value = model_ep if key == "model_ep" and model_ep else field.get("default")
        if not isinstance(value, str) or not value:
            raise PlatformAPIError(f"platform execution contract has no default for {key}")
        if "enum" in field and value not in field["enum"]:
            raise PlatformAPIError(f"platform {key} value is not in its allowed enum")
        values[key] = value
    if "request_source" not in properties:
        raise PlatformAPIError("platform contract does not support request_source")
    values["request_source"] = request_source
    missing = set(schema.get("required", [])) - values.keys()
    if missing:
        raise PlatformAPIError(f"platform contract requires unsupported fields: {sorted(missing)}")
    execution["values"] = values

    experiments = [
        item
        for item in _rows(experiment_data, "experiments")
        if item.get("workflow_id") == VIKING_WORKFLOW
        and item.get("component_type") == "memory"
        and item.get("status") == "active"
        and item.get("agent_id", agent_id) == agent_id
        and (not experiment_id or item.get("experiment_id") == experiment_id)
    ]
    experiment = _unique(
        experiments, "active external-memory experiment (training_task.experiment_id)"
    )
    selected_id = str(experiment.get("experiment_id") or "")
    if not selected_id:
        raise PlatformAPIError("resolved experiment has no experiment_id")
    target_data = await client.get_experiment_targets(agent_id, selected_id)
    target = _unique(
        [row for row in _rows(target_data, "targets") if row.get("component_type") == "memory"],
        "memory target",
    )
    if not target.get("target"):
        raise PlatformAPIError("resolved memory target has no target")

    return {
        # V2 unified scheduling chooses routing separately from capacity.
        # Do not mix agent lane/resource binding with resource_requests.
        "schema_version": "training-task-request.v2",
        "name": name,
        "agent": {
            "agent_id": agent_id,
            "execution": execution,
        },
        "experiment_id": selected_id,
        "target": {"component_type": "memory", "target": target["target"]},
        # This adapter implements the Viking suite request/result protocol.
        "evaluator": {"evaluator_id": "viking_experiment_suite@v1", "workers": concurrency},
        "viking_experiment_sets": sets,
        # The external-training workflow requires these three API fields to be
        # exactly 1. They do not schedule Train or control the native runner's
        # loops. Keep its actual plan (including epochs=0) in ArkRun instead.
        "memory": {"train_epochs": 1, "train_trials": 1, "eval_trials": 1},
        "execution": {"rollout_workers": concurrency, "case_timeout_seconds": case_timeout_seconds},
        "scheduling": scheduling,
    }


async def build_task_scheduling(
    client: TrainingPlatformClient,
    *,
    agent_id: str,
    lane_key: str,
    rollout_resource_id: str,
    concurrency: int,
    workflow_id: str = VIKING_WORKFLOW,
    require_memory: bool | None = None,
) -> dict[str, Any]:
    """Resolve independent execution routing and unified resource requests."""
    for key, value in (
        ("agent_id", agent_id),
        ("lane_key", lane_key),
        ("rollout_resource_id", rollout_resource_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"training_task.{key} is required")
    if type(concurrency) is not int or not 1 <= concurrency <= 1_000_000:
        raise ValueError("concurrency must be an integer between 1 and 1000000")
    agent_id, lane_key, rollout_resource_id = (
        agent_id.strip(),
        lane_key.strip(),
        rollout_resource_id.strip(),
    )
    resource_data, lane_data = await asyncio.gather(
        client.list_resources(agent_id), client.list_lanes(agent_id)
    )
    _unique(
        [
            row
            for row in _rows(lane_data, "lanes")
            if row.get("lane_key") == lane_key
            and row.get("agent_id", agent_id) == agent_id
            and row.get("enabled", True)
        ],
        f"enabled lane for agent={agent_id!r} lane_key={lane_key!r}",
    )
    resources = [
        row for row in _rows(resource_data, "resources") if _applicable(row, agent_id, workflow_id)
    ]
    rollout = _unique(
        [
            row
            for row in resources
            if row.get("resource_id") == rollout_resource_id
            and row.get("resource_type") == "rollout_concurrency"
            and row.get("agent_id") == agent_id
            and row.get("scope") in {"lane", "agent"}
        ],
        f"Ark rollout resource for agent={agent_id!r} resource_id={rollout_resource_id!r}",
    )
    requests = [_resource_request(rollout, concurrency)]
    if require_memory is None:
        require_memory = workflow_id == VIKING_WORKFLOW
    if require_memory:
        identities = [row for row in resources if row.get("resource_type") == "memory_identity"]
        required_identities = [row for row in identities if row.get("required_for_task")]
        identity = _unique(required_identities or identities, "applicable memory_identity resource")
        requests.append(_resource_request(identity, 1))
    selected_ids = {item["resource_id"] for item in requests}
    if len(selected_ids) != len(requests):
        raise PlatformAPIError("platform resource IDs must be unique; no Task created")
    for resource in resources:
        if not resource.get("required_for_task"):
            continue
        resource_id = resource.get("resource_id")
        if resource_id in selected_ids:
            continue
        if resource.get("resource_type") == "rollout_concurrency":
            raise PlatformAPIError(
                f"required rollout resource {resource_id!r} conflicts with selected "
                f"resource {rollout_resource_id!r}; exactly one is allowed; no Task created"
            )
        amount = resource.get("default_amount")
        if not _valid_amount(amount):
            config = _object(resource.get("config"), "resource.config")
            automatic = _object(
                config.get("automatic_request"), "resource.config.automatic_request"
            )
            amount = automatic.get("amount")
        if not _valid_amount(amount):
            raise PlatformAPIError(
                f"required resource {resource_id!r} has no positive default_amount or "
                "automatic_request.amount; no Task created"
            )
        requests.append(_resource_request(resource, amount))
        selected_ids.add(resource_id)
    if len(requests) > 100:
        raise PlatformAPIError("platform requires more than 100 resources; no Task created")
    return {"priority": 100, "lane_key": lane_key, "resource_requests": requests}


def _valid_amount(value: Any) -> bool:
    return type(value) is int and 1 <= value <= 1_000_000


def _resource_request(resource: dict[str, Any], amount: int) -> dict[str, Any]:
    resource_id = resource.get("resource_id")
    if not isinstance(resource_id, str) or not 1 <= len(resource_id.strip()) <= 64:
        raise PlatformAPIError("resolved resource has invalid resource_id; no Task created")
    return {"resource_id": resource_id.strip(), "amount": amount}


def _rows(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    rows = data.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise PlatformAPIError(f"platform metadata must contain a {key} list")
    return rows


def _object(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PlatformAPIError(f"platform {label} must be an object")
    return value


def _unique(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(rows) != 1:
        raise PlatformAPIError(f"expected exactly one {label}, found {len(rows)}; no Task created")
    return rows[0]


def _applicable(resource: dict[str, Any], agent_id: str, workflow_id: str) -> bool:
    if not resource.get("enabled", True):
        return False
    if resource.get("agent_id") and resource["agent_id"] != agent_id:
        return False
    agents = resource.get("applicable_agent_ids") or []
    workflows = resource.get("applicable_workflow_ids") or []
    return (not agents or agent_id in agents) and (not workflows or workflow_id in workflows)
