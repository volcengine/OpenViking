#!/usr/bin/env python3
"""HTTP client for the Ark external-training platform APIs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import httpx

_TERMINAL_TASK_STATUSES = {"succeeded", "completed", "failed", "canceled", "cancelled"}
_TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


class PlatformAPIError(RuntimeError):
    """A platform API returned an unexpected response."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class PlatformClientConfig:
    gateway_base_url: str
    api_key: str | None = None
    project_id: str | None = None
    vaka_request_source: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 120.0

    def __post_init__(self) -> None:
        self.gateway_base_url = _required_url(self.gateway_base_url, "gateway_base_url")
        if self.vaka_request_source is not None:
            self.vaka_request_source = str(self.vaka_request_source).strip()
            if not self.vaka_request_source:
                raise ValueError("vaka_request_source must not be empty")
            if "\r" in self.vaka_request_source or "\n" in self.vaka_request_source:
                raise ValueError("vaka_request_source must not contain CR/LF")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")


class TrainingPlatformClient:
    """Call all CaseHub and training APIs through the unified inspect gateway."""

    def __init__(
        self,
        config: PlatformClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        headers = dict(config.headers)
        for key in list(headers):
            if key.lower() == "x-vaka-request-source":
                del headers[key]
        if config.vaka_request_source:
            headers["x-vaka-request-source"] = config.vaka_request_source
        if config.api_key:
            headers.setdefault("X-API-Key", config.api_key)
        if config.project_id:
            headers.setdefault("X-Project-Id", config.project_id)

        self._gateway = httpx.AsyncClient(
            base_url=config.gateway_base_url,
            headers=headers,
            timeout=config.timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> "TrainingPlatformClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()

    async def close(self) -> None:
        await self._gateway.aclose()

    async def create_training_task(self, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._gateway.post("/inspect/training/tasks", json=body)
        payload = _response_payload(response, operation="create training task")
        data = _unwrap_data(payload)
        task_id = _first_non_empty(data, "task_id", "id")
        if task_id is None:
            raise PlatformAPIError("create training task response has no task_id")
        return dict(data)

    async def get_execution_contract(self, agent_id: str) -> dict[str, Any]:
        return await self._get_metadata(f"/inspect/training/agents/{agent_id}/execution-contract")

    async def list_experiments(self, agent_id: str) -> dict[str, Any]:
        return await self._get_metadata(f"/inspect/training/agents/{agent_id}/experiments")

    async def get_experiment_targets(self, agent_id: str, experiment_id: str) -> dict[str, Any]:
        return await self._get_metadata(
            f"/inspect/training/agents/{agent_id}/experiments/{experiment_id}/targets"
        )

    async def list_resources(self, agent_id: str) -> dict[str, Any]:
        return await self._get_metadata(
            "/inspect/resources/resources",
            params={"applicable_agent_id": agent_id, "include_disabled": "false"},
        )

    async def _get_metadata(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            response = await self._gateway.get(path, params=params)
        except httpx.TransportError as exc:
            raise PlatformAPIError(
                f"could not query platform metadata {path}: {type(exc).__name__}"
            ) from exc
        return dict(_unwrap_data(_response_payload(response, operation=f"get {path}")))

    async def resolve_rollout_lane_resource(
        self,
        *,
        agent_id: str,
        lane_key: str,
    ) -> dict[str, Any]:
        response = await self._gateway.get(
            "/inspect/resources/resources",
            params={
                "applicable_agent_id": agent_id,
                "include_disabled": "false",
            },
        )
        data = _unwrap_data(_response_payload(response, operation=f"resolve agent lane {lane_key}"))
        resources = data.get("resources")
        if not isinstance(resources, list):
            raise PlatformAPIError("resource response must contain resources")
        matches = [
            dict(resource)
            for resource in resources
            if isinstance(resource, dict)
            and resource.get("enabled", True)
            and str(resource.get("resource_type") or "") == "rollout_concurrency"
            and str(resource.get("scope") or "") == "lane"
            and str(resource.get("agent_id") or "") == agent_id
            and str(resource.get("lane_key") or "") == lane_key
        ]
        if len(matches) != 1:
            raise PlatformAPIError(
                f"expected exactly one rollout_concurrency resource for "
                f"agent={agent_id!r} lane={lane_key!r}, found {len(matches)}"
            )
        return matches[0]

    async def get_training_task(self, task_id: str) -> dict[str, Any]:
        response = await self._gateway.get(f"/inspect/training/tasks/{task_id}")
        return dict(
            _unwrap_data(_response_payload(response, operation=f"get training task {task_id}"))
        )

    async def wait_for_ov_wait(
        self,
        task_id: str,
        *,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: float = 900.0,
    ) -> dict[str, Any]:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        transient_attempts = 0
        while True:
            try:
                task = await self.get_training_task(task_id)
                transient_attempts = 0
            except (httpx.TransportError, PlatformAPIError) as exc:
                if not _is_transient_error(exc) or loop.time() >= deadline:
                    raise
                transient_attempts += 1
                await asyncio.sleep(min(poll_interval_seconds * transient_attempts, 10.0))
                continue

            status = str(task.get("status") or "").strip().lower()
            current_step = str(task.get("current_step") or "").strip()
            wait_steps = ("OV_WAIT", "VIKING_OV_WAIT")
            wait_records = [_find_step(task.get("steps"), step) for step in wait_steps]
            wait_running = any(
                str((record or {}).get("status") or "").strip().lower() == "running"
                for record in wait_records
            )
            if current_step in wait_steps and status not in _TERMINAL_TASK_STATUSES:
                return task
            if wait_running and status not in _TERMINAL_TASK_STATUSES:
                return task
            if status in _TERMINAL_TASK_STATUSES:
                raise PlatformAPIError(
                    f"training task {task_id} reached terminal status {status!r} "
                    "before its external wait node"
                )
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"training task {task_id} did not reach an external wait node "
                    f"within {timeout_seconds}s; "
                    f"last status={status!r} current_step={current_step!r}"
                )
            await asyncio.sleep(poll_interval_seconds)

    async def list_cases(
        self,
        *,
        caseset_id: str | None,
        dataset_id: str | None,
        limit: int,
        offset: int,
    ) -> Any:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if caseset_id:
            params["caseset_id"] = caseset_id
        if dataset_id:
            params["dataset_id"] = dataset_id
        response = await self._gateway.get("/inspect/casehub/cases", params=params)
        return _response_payload(response, operation="list CaseHub cases")

    async def get_case(self, case_id: str) -> Any:
        response = await self._gateway.get(f"/inspect/casehub/cases/{case_id}")
        return _response_payload(response, operation=f"get CaseHub case {case_id}")

    async def list_rollout_source_cases(
        self,
        task_id: str,
        *,
        phase: str,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        response = await self._gateway.get(
            f"/inspect/training/tasks/{task_id}/rollout-source-cases",
            params={"phase": phase, "page": page, "page_size": page_size},
        )
        return dict(
            _unwrap_data(
                _response_payload(
                    response,
                    operation=f"list {phase} rollout source cases for {task_id}",
                )
            )
        )

    async def submit_rollout_eval(
        self,
        task_id: str,
        *,
        body: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = await self._gateway.post(
            f"/inspect/training/tasks/{task_id}/rollout-eval",
            json=body,
            headers={"Idempotency-Key": idempotency_key},
        )
        data = _unwrap_data(
            _response_payload(response, operation=f"submit rollout eval for {task_id}")
        )
        batch_rollout_id = _first_non_empty(data, "batch_rollout_id")
        handles = data.get("case_rollouts")
        if batch_rollout_id is None or not isinstance(handles, list) or not handles:
            raise PlatformAPIError(
                "rollout-eval response must contain batch_rollout_id and case_rollouts"
            )
        return dict(data)

    async def get_case_rollout(self, task_id: str, case_rollout_id: str) -> dict[str, Any]:
        response = await self._gateway.get(
            f"/inspect/training/tasks/{task_id}/rollout-eval/{case_rollout_id}"
        )
        return dict(
            _unwrap_data(
                _response_payload(
                    response,
                    operation=f"get case rollout {case_rollout_id}",
                )
            )
        )

    async def complete_external_training(
        self,
        task_id: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = await self._gateway.post(
            f"/inspect/training/tasks/{task_id}/signals/external-training-completed",
            headers={"Idempotency-Key": idempotency_key},
        )
        if response.status_code in {409, 422}:
            task = await self.get_training_task(task_id)
            status = str(task.get("status") or "").strip().lower()
            if status in {"succeeded", "completed"}:
                return {
                    "task_id": task_id,
                    "status": status,
                    "signal": "external-training-completed",
                    "reconciled": True,
                }
        return dict(
            _unwrap_data(
                _response_payload(
                    response,
                    operation=f"complete external training {task_id}",
                )
            )
        )


def extract_case_items(payload: Any) -> list[dict[str, Any]]:
    """Extract CaseHub case rows from common response envelope variants."""

    candidates: list[Any] = [payload]
    if isinstance(payload, dict):
        candidates.extend(payload.get(key) for key in ("data", "result") if key in payload)
    for candidate in candidates:
        if isinstance(candidate, list):
            return [dict(item) for item in candidate if isinstance(item, dict)]
        if not isinstance(candidate, dict):
            continue
        for key in ("cases", "items", "records", "list"):
            value = candidate.get(key)
            if isinstance(value, list):
                return [dict(item) for item in value if isinstance(item, dict)]
        nested = candidate.get("data")
        if isinstance(nested, dict):
            for key in ("cases", "items", "records", "list"):
                value = nested.get(key)
                if isinstance(value, list):
                    return [dict(item) for item in value if isinstance(item, dict)]
    return []


def extract_case_detail(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformAPIError("CaseHub case detail must be an object")
    current = payload
    for _ in range(3):
        if any(key in current for key in ("case_id", "name", "envelope", "payload")):
            return dict(current)
        nested = current.get("data")
        if not isinstance(nested, dict):
            break
        current = nested
    return dict(current)


def _response_payload(response: httpx.Response, *, operation: str) -> Any:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise PlatformAPIError(
            f"{operation} returned non-JSON HTTP {response.status_code}",
            status_code=response.status_code,
        ) from exc
    if response.is_error:
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise PlatformAPIError(
            f"{operation} failed with HTTP {response.status_code}: {detail}",
            status_code=response.status_code,
        )
    return payload


def _unwrap_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise PlatformAPIError("platform response must be an object")
    data = payload.get("data")
    if isinstance(data, dict) and ("ts" in payload or len(payload) == 1):
        return data
    return payload


def _first_non_empty(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _find_step(raw_steps: Any, step_name: str) -> dict[str, Any] | None:
    if not isinstance(raw_steps, list):
        return None
    for raw in raw_steps:
        if isinstance(raw, dict) and str(raw.get("step") or "") == step_name:
            return raw
    return None


def _is_transient_error(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    return isinstance(exc, PlatformAPIError) and exc.status_code in _TRANSIENT_STATUS_CODES


def _required_url(value: str, label: str) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise ValueError(f"{label} is required")
    return text
