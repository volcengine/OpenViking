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
    lane_key: str,
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
    if not lane_key.strip():
        raise ValueError("training_task.lane_key is required")
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
    contract_data, experiment_data, resource_data = await asyncio.gather(
        client.get_execution_contract("ark"),
        client.list_experiments("ark"),
        client.list_resources("ark"),
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
        and item.get("agent_id", "ark") == "ark"
        and (not experiment_id or item.get("experiment_id") == experiment_id)
    ]
    experiment = _unique(
        experiments, "active external-memory experiment (training_task.experiment_id)"
    )
    selected_id = str(experiment.get("experiment_id") or "")
    if not selected_id:
        raise PlatformAPIError("resolved experiment has no experiment_id")
    target_data = await client.get_experiment_targets("ark", selected_id)
    target = _unique(
        [row for row in _rows(target_data, "targets") if row.get("component_type") == "memory"],
        "memory target",
    )
    if not target.get("target"):
        raise PlatformAPIError("resolved memory target has no target")

    resources = [row for row in _rows(resource_data, "resources") if _applicable(row)]
    lane = _unique(
        [
            row
            for row in resources
            if row.get("resource_type") == "rollout_concurrency"
            and row.get("agent_id") == "ark"
            and row.get("scope") in {"lane", "agent"}
            and (
                row.get("lane_key") or _object(row.get("config"), "resource.config").get("lane_key")
            )
            == lane_key
        ],
        f"Ark rollout resource for lane {lane_key!r}",
    )
    requests = [{"resource_id": _resource_id(lane), "amount": concurrency}]
    for resource in resources:
        if resource is lane or not resource.get("required_for_task"):
            continue
        if resource.get("resource_type") == "rollout_concurrency":
            raise PlatformAPIError(
                f"required rollout resource {_resource_id(resource)} conflicts with selected lane {lane_key}"
            )
        amount = resource.get("default_amount", 0)
        config = _object(resource.get("config"), "resource.config")
        automatic = _object(config.get("automatic_request"), "resource.config.automatic_request")
        if not amount:
            amount = automatic.get("amount", 0)
        if not amount and resource.get("resource_type") == "memory_identity":
            # One platform identity for the shared local Memory space, as before.
            amount = 1
        if type(amount) is not int or amount <= 0:
            raise PlatformAPIError(f"required resource {_resource_id(resource)} has no safe amount")
        requests.append({"resource_id": _resource_id(resource), "amount": amount})

    return {
        "schema_version": "training-task-request.v2",
        "name": name,
        "agent": {"agent_id": "ark", "execution": execution},
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
        "scheduling": {"priority": 100, "resource_requests": requests},
    }


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


def _applicable(resource: dict[str, Any]) -> bool:
    if not resource.get("enabled", True):
        return False
    if resource.get("agent_id") and resource["agent_id"] != "ark":
        return False
    agents = resource.get("applicable_agent_ids") or []
    workflows = resource.get("applicable_workflow_ids") or []
    return (not agents or "ark" in agents) and (not workflows or VIKING_WORKFLOW in workflows)


def _resource_id(resource: dict[str, Any]) -> str:
    value = str(resource.get("resource_id") or "").strip()
    if not value:
        raise PlatformAPIError("resolved resource has no resource_id")
    return value
