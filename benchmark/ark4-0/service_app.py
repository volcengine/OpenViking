#!/usr/bin/env python3
"""Generic OpenViking dataset-service facade over the Ark platform APIs."""

from __future__ import annotations

import asyncio
import hmac
import json
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from case_loader import (
    ArkCaseLoader,
    ArkCaseRepository,
    VikingCaseRepository,
    task_indices_from_filters,
)
from fastapi import FastAPI, HTTPException, Request
from memory_proxy import MemoryProxyConfig, install_memory_proxy
from platform_client import PlatformAPIError, TrainingPlatformClient
from pydantic import BaseModel, Field
from rollout_executor import ArkRolloutExecutor, RolloutBatchCoordinator
from task_request import build_viking_task_request

from openviking.session.train.components.dataset_service import create_dataset_service_app


@dataclass(slots=True)
class ArkAdapterServiceConfig:
    dataset: str
    domain: str
    task_name: str = "openviking_ark4_external_training"
    workflow_id: str = "ov_external_training"
    task_body: dict[str, Any] = field(default_factory=dict)
    viking_experiment_sets: list[dict[str, Any]] = field(default_factory=list)
    lane_key: str = ""
    model_ep: str = ""
    experiment_id: str = ""
    request_source: str = ""
    existing_task_id: str = ""
    agent_id: str = "ark"
    agent_lane_key: str = ""
    agent_execution: dict[str, Any] = field(default_factory=dict)
    evaluator_id: str = "rollout_builtin@v1"
    task_ready_poll_interval_seconds: float = 2.0
    task_ready_timeout_seconds: float = 900.0
    rollout_concurrency: int = 16
    rollout_poll_interval_seconds: float = 2.0
    rollout_timeout_seconds: float = 3600.0
    connector_config: dict[str, Any] = field(default_factory=dict)
    runtime_params: dict[str, Any] = field(default_factory=dict)
    extra_header: dict[str, Any] = field(default_factory=dict)
    idempotency_namespace: str = "openviking-ark4"
    require_messages_for_training: bool = True
    admin_token: str = ""
    memory_proxy: MemoryProxyConfig = field(default_factory=MemoryProxyConfig)

    def __post_init__(self) -> None:
        if not str(self.dataset or "").strip():
            raise ValueError("dataset is required")
        if not str(self.domain or "").strip():
            raise ValueError("domain is required")
        if self.rollout_concurrency <= 0:
            raise ValueError("rollout_concurrency must be > 0")


class CaseHubRunSelection(BaseModel):
    dataset_ids: list[str] = Field(default_factory=list)
    case_ids: list[str] = Field(default_factory=list)
    task_dataset_ids: list[str] = Field(default_factory=list)


class TrainingPlan(BaseModel):
    train_epochs: int = Field(ge=0, strict=True)
    train_trials: int = Field(ge=1, strict=True)
    eval_trials: int = Field(ge=1, strict=True)


class StartRunRequest(BaseModel):
    run_id: str
    dataset: str
    domain: str
    concurrency: int | None = Field(default=None, ge=1)
    training_plan: TrainingPlan | None = None
    casehub: CaseHubRunSelection = Field(default_factory=CaseHubRunSelection)


@dataclass(slots=True)
class ArkRun:
    run_id: str
    task_id: str
    status: str
    task: dict[str, Any]
    casehub_dataset_ids: list[str]
    casehub_case_ids: list[str]
    task_casehub_dataset_ids: list[str]
    case_count: int
    concurrency: int
    repository: ArkCaseRepository | VikingCaseRepository | None = field(repr=False)
    training_plan: dict[str, int] | None = None
    resolved_task_request: dict[str, Any] = field(default_factory=dict, repr=False)
    completion: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "status": self.status,
            "casehub_dataset_ids": self.casehub_dataset_ids,
            "casehub_case_ids": self.casehub_case_ids,
            "task_casehub_dataset_ids": self.task_casehub_dataset_ids,
            "case_count": self.case_count,
            "concurrency": self.concurrency,
            "task": self.task,
            "completion": self.completion,
            "training_plan": self.training_plan,
            "resolved_task_request": self.resolved_task_request,
        }


class ArkRunRegistry:
    """Bind one native train/eval invocation to one platform Task."""

    def __init__(self, client: TrainingPlatformClient, config: ArkAdapterServiceConfig) -> None:
        self._client = client
        self._config = config
        self._runs: dict[str, ArkRun] = {}
        self._lock = asyncio.Lock()

    async def start(self, request: StartRunRequest) -> ArkRun:
        run_id = request.run_id.strip()
        if not run_id:
            raise ValueError("run_id is required")
        if request.dataset != self._config.dataset:
            raise ValueError(f"Unsupported dataset: {request.dataset}")
        if request.domain != self._config.domain:
            raise ValueError(f"Unsupported domain: {request.domain}")
        dataset_ids = _normalized_ids(request.casehub.dataset_ids, label="dataset_ids")
        case_ids = _normalized_ids(request.casehub.case_ids, label="case_ids")
        task_dataset_ids = _normalized_ids(
            request.casehub.task_dataset_ids or dataset_ids,
            label="task_dataset_ids",
        )
        is_viking_external = self._config.workflow_id == "ark_viking_external_training"
        training_plan = request.training_plan.model_dump() if request.training_plan else None
        if not dataset_ids and not is_viking_external:
            raise ValueError("casehub.dataset_ids is required")
        unknown_task_dataset_ids = [
            dataset_id for dataset_id in task_dataset_ids if dataset_id not in dataset_ids
        ]
        if unknown_task_dataset_ids:
            raise ValueError(
                "casehub.task_dataset_ids must be a subset of casehub.dataset_ids: "
                + ", ".join(unknown_task_dataset_ids)
            )
        async with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if (
                    existing.casehub_dataset_ids != dataset_ids
                    or existing.casehub_case_ids != case_ids
                    or existing.task_casehub_dataset_ids != task_dataset_ids
                    or existing.concurrency
                    != (request.concurrency or self._config.rollout_concurrency)
                    or existing.training_plan != training_plan
                ):
                    raise ValueError(
                        f"benchmark run {run_id} already exists with a different CaseHub selection, "
                        "concurrency or training plan"
                    )
                return existing
            if is_viking_external:
                body = deepcopy(self._config.task_body)
                existing_task_id = str(self._config.existing_task_id or "").strip()
                if not body and not existing_task_id and not self._config.viking_experiment_sets:
                    raise ValueError(
                        "training_task.viking_experiment_sets or training_task.existing_task_id is required "
                        "for ark_viking_external_training"
                    )
                if existing_task_id:
                    if self._runs:
                        raise ValueError(
                            "training_task.existing_task_id can only be attached to one run"
                        )
                elif self._config.viking_experiment_sets:
                    if training_plan is None:
                        raise ValueError(
                            "runner must send training_plan; use the updated run_batch_train_eval"
                        )
                    body = await build_viking_task_request(
                        self._client,
                        name=f"{self._config.task_name}_{run_id}",
                        lane_key=self._config.lane_key,
                        experiment_sets=self._config.viking_experiment_sets,
                        concurrency=request.concurrency or self._config.rollout_concurrency,
                        training_plan=training_plan,
                        request_source=self._config.request_source,
                        case_timeout_seconds=self._config.runtime_params.get("task_timeout", 3600),
                        model_ep=self._config.model_ep,
                        experiment_id=self._config.experiment_id,
                    )
                    print(
                        "[ark4-adapter] resolved execution: "
                        + json.dumps(body["agent"]["execution"], ensure_ascii=False),
                        flush=True,
                    )
                else:
                    name_key = "name" if str(body.get("schema_version") or "") else "task_name"
                    base_name = str(body.get(name_key) or self._config.task_name).strip()
                    body[name_key] = f"{base_name}_{run_id}"
            else:
                repository = ArkCaseRepository(
                    client=self._client,
                    dataset_ids=dataset_ids,
                    case_ids=case_ids,
                )
                cases = await repository.all_cases()
                mismatched_case_ids = [
                    str(case.metadata.get("platform_case_id") or case.name)
                    for case in cases
                    if case.metadata.get("dataset_id")
                    and str(case.metadata["dataset_id"]) not in dataset_ids
                ]
                if mismatched_case_ids:
                    raise ValueError(
                        "CaseHub case(s) do not belong to the selected dataset(s): "
                        + ", ".join(mismatched_case_ids)
                    )
                workers = request.concurrency or self._config.rollout_concurrency
                body = {
                    "task_name": f"{self._config.task_name}_{run_id}",
                    "workflow_id": self._config.workflow_id,
                    "agent_id": self._config.agent_id,
                    "casehub_dataset_ids": task_dataset_ids,
                    "evaluator_id": self._config.evaluator_id,
                    "workers": workers,
                }
                if self._config.agent_lane_key:
                    lane_resource = await self._client.resolve_rollout_lane_resource(
                        agent_id=self._config.agent_id,
                        lane_key=self._config.agent_lane_key,
                    )
                    resource_id = str(lane_resource.get("resource_id") or "").strip()
                    if not resource_id:
                        raise PlatformAPIError("resolved lane resource has no resource_id")
                    body["scheduling"] = {
                        "resource_requests": [
                            {
                                "resource_id": resource_id,
                                "amount": workers,
                                "metadata": {},
                            }
                        ]
                    }
                if self._config.agent_execution:
                    body["agent_execution"] = dict(self._config.agent_execution)
            created = (
                await self._client.get_training_task(existing_task_id)
                if is_viking_external and existing_task_id
                else await self._client.create_training_task(body)
            )
            task_id = str(created["task_id"])
            if is_viking_external:
                repository = VikingCaseRepository(
                    client=self._client,
                    task_id=task_id,
                    case_ids=case_ids,
                )
                cases = []
            run = ArkRun(
                run_id=run_id,
                task_id=task_id,
                status="created",
                task=created,
                casehub_dataset_ids=dataset_ids,
                casehub_case_ids=case_ids,
                task_casehub_dataset_ids=task_dataset_ids,
                case_count=len(cases),
                concurrency=request.concurrency or self._config.rollout_concurrency,
                repository=repository,
                training_plan=training_plan,
                resolved_task_request=body if is_viking_external and not existing_task_id else {},
            )
            self._runs[run_id] = run
            ready = await self._client.wait_for_ov_wait(
                task_id,
                poll_interval_seconds=self._config.task_ready_poll_interval_seconds,
                timeout_seconds=self._config.task_ready_timeout_seconds,
            )
            run.task = ready
            run.status = "ov_wait"
            if is_viking_external:
                train_cases = await repository.cases_for_split("train")
                if not train_cases:
                    raise ValueError("Viking train phase returned no selected cases")
                run.case_count = len(train_cases)
            print(
                f"[ark4-adapter] run {run_id} created platform task {task_id}; OV_WAIT ready",
                flush=True,
            )
            return run

    def get(self, run_id: str) -> ArkRun | None:
        return self._runs.get(run_id)

    def all(self) -> list[ArkRun]:
        return list(self._runs.values())

    async def complete(self, run_id: str) -> ArkRun:
        async with self._lock:
            run = self._runs.get(run_id)
            if run is None:
                raise KeyError(run_id)
            if run.status == "completed":
                return run
            completion = await self._client.complete_external_training(
                run.task_id,
                idempotency_key=(f"{self._config.idempotency_namespace}:{run.task_id}:complete"),
            )
            run.completion = completion
            run.status = "completed"
            run.repository = None
            print(
                f"[ark4-adapter] run {run_id} completed platform task {run.task_id}",
                flush=True,
            )
            return run


def create_app(
    *,
    client: TrainingPlatformClient,
    config: ArkAdapterServiceConfig,
) -> FastAPI:
    """Create the localhost compatibility service consumed by run_batch_train_eval."""

    is_viking_external = config.workflow_id == "ark_viking_external_training"

    def make_case_loader(
        dataset: str,
        domain: str,
        split: str,
        filters: dict[str, Any],
    ) -> ArkCaseLoader:
        if dataset != config.dataset:
            raise ValueError(f"Unsupported dataset: {dataset}")
        if domain != config.domain:
            raise ValueError(f"Unsupported domain: {domain}")
        run_id = str(filters.pop("_openviking_benchmark_run_id", "")).strip()
        requested_dataset_ids = _normalized_ids(
            filters.pop("_openviking_casehub_dataset_ids", []),
            label="_openviking_casehub_dataset_ids",
        )
        run = run_registry.get(run_id)
        if run is None or run.repository is None:
            raise ValueError(
                "case query has no active benchmark run; start it through /v1/runs/start first"
            )
        if run.status != "ov_wait":
            raise ValueError(f"benchmark run {run_id} is not active: {run.status}")
        unknown_dataset_ids = (
            []
            if is_viking_external
            else [
                dataset_id
                for dataset_id in requested_dataset_ids
                if dataset_id not in run.casehub_dataset_ids
            ]
        )
        if unknown_dataset_ids:
            raise ValueError(
                "case query dataset(s) are outside the active benchmark run: "
                + ", ".join(unknown_dataset_ids)
            )
        return ArkCaseLoader(
            repository=run.repository,
            split=split,
            task_indices=task_indices_from_filters(filters),
            dataset_ids=None if is_viking_external else requested_dataset_ids or None,
        )

    def annotate_case_batch(cases: list[Any], request: Any) -> None:
        if not cases:
            return
        case_ids = [
            str(case.metadata.get("platform_case_id") or case.input.get("task_id") or "").strip()
            for case in cases
        ]
        if any(not case_id for case_id in case_ids):
            raise ValueError("Ark batch case is missing platform_case_id")
        run_id = str((request.filters or {}).get("_openviking_benchmark_run_id") or "").strip()
        payload = {
            "run_id": run_id,
            "split": request.split,
            "cursor": request.cursor,
            "case_ids": case_ids,
        }
        batch_id = sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        descriptor = {"batch_id": batch_id, "case_ids": case_ids}
        for case in cases:
            case.metadata["_ark_rollout_batch"] = descriptor

    run_registry = ArkRunRegistry(client, config)
    batch_coordinator = RolloutBatchCoordinator()

    def make_rollout_executor(options: dict[str, Any]) -> ArkRolloutExecutor:
        run_id = str(options.pop("_openviking_benchmark_run_id", "")).strip()
        run = run_registry.get(run_id)
        if run is None:
            raise ValueError(
                "rollout has no active benchmark run; start it through /v1/runs/start first"
            )
        if run.status != "ov_wait":
            raise ValueError(f"benchmark run {run_id} is not active: {run.status}")
        return ArkRolloutExecutor(
            client=client,
            platform_task_id=run.task_id,
            connector_config=config.connector_config,
            runtime_params=config.runtime_params,
            extra_header=config.extra_header,
            poll_interval_seconds=config.rollout_poll_interval_seconds,
            timeout_seconds=config.rollout_timeout_seconds,
            idempotency_namespace=config.idempotency_namespace,
            require_messages_for_training=config.require_messages_for_training,
            concurrency=run.concurrency,
            batch_coordinator=batch_coordinator,
        )

    app = create_dataset_service_app(
        service_name="ark4-platform-adapter",
        make_case_loader=make_case_loader,
        make_rollout_executor=make_rollout_executor,
        on_cases_queried=annotate_case_batch,
        max_rollout_concurrency=None,
        rollout_thread_workers=None,
    )
    app.state.run_registry = run_registry
    app.state.dataset = config.dataset
    app.state.domain = config.domain
    memory_proxy = install_memory_proxy(app, config.memory_proxy)

    def authorize_admin(request: Request) -> None:
        if not config.admin_token:
            return
        supplied = request.headers.get("X-Ark4-Admin-Token", "")
        if not hmac.compare_digest(supplied, config.admin_token):
            raise HTTPException(status_code=401, detail="invalid Ark4 admin token")

    @app.post("/v1/runs/start")
    async def start_run(request: StartRunRequest) -> dict[str, Any]:
        try:
            return (await run_registry.start(request)).to_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PlatformAPIError as exc:
            status_code = 400 if exc.status_code == 404 else 502
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @app.post("/v1/runs/{run_id}/complete")
    async def complete_run(run_id: str) -> dict[str, Any]:
        try:
            return (await run_registry.complete(run_id)).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc

    @app.get("/admin/platform-runs")
    async def get_platform_runs(request: Request) -> dict[str, Any]:
        authorize_admin(request)
        return {"runs": [run.to_dict() for run in run_registry.all()]}

    @app.get("/admin/platform-runs/{run_id}")
    async def get_platform_run(run_id: str, request: Request) -> dict[str, Any]:
        authorize_admin(request)
        run = run_registry.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"run not found: {run_id}")
        task = await client.get_training_task(run.task_id)
        return {**run.to_dict(), "task": task}

    @app.get("/admin/memory-proxy")
    async def get_memory_proxy(request: Request) -> dict[str, Any]:
        authorize_admin(request)
        return {
            "enabled": memory_proxy is not None,
            "openviking_target": config.memory_proxy.openviking_target,
            "openviking_url": config.memory_proxy.openviking_url,
            "event_log_file": (
                str(config.memory_proxy.event_log_file)
                if config.memory_proxy.event_log_file is not None
                else None
            ),
            "call_count": memory_proxy.call_count if memory_proxy is not None else 0,
        }

    return app


def _normalized_ids(values: list[str], *, label: str) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"casehub.{label} must not contain empty values")
        if text not in result:
            result.append(text)
    return result
