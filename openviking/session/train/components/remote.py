# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP-backed CaseLoader and RolloutExecutor implementations."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from openviking.session.train.components.dataset_service import (
    case_from_dict,
    case_to_dict,
    policy_set_to_dict,
    rollout_from_dict,
)
from openviking.session.train.components.progress import run_with_progress
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    ExperienceSet,
    Rollout,
)


@dataclass(slots=True)
class RemoteCaseLoader:
    """Load Case batches from a benchmark/environment HTTP service."""

    service_url: str
    dataset: str
    domain: str
    split: str
    batch_size: int | None = None
    limit: int | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 60.0

    async def batches(self, context: Any = None) -> AsyncIterator[list[Case]]:
        del context
        if self.batch_size is not None and self.batch_size <= 0:
            raise ValueError("batch_size must be > 0")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("limit must be > 0")

        remaining = self.limit
        cursor: str | None = None
        async with httpx.AsyncClient(base_url=self.service_url.rstrip("/"), timeout=self.timeout_seconds) as client:
            while True:
                request_limit = self.batch_size or remaining
                if request_limit is None:
                    request_limit = 100
                if remaining is not None:
                    request_limit = min(request_limit, remaining)
                if request_limit <= 0:
                    return
                response = await client.post(
                    "/v1/cases/query",
                    json={
                        "dataset": self.dataset,
                        "domain": self.domain,
                        "split": self.split,
                        "cursor": cursor,
                        "limit": request_limit,
                        "filters": self.filters,
                    },
                )
                response.raise_for_status()
                data = response.json()
                cases = [case_from_dict(item) for item in data.get("cases", [])]
                if not cases:
                    return
                yield cases
                if remaining is not None:
                    remaining -= len(cases)
                    if remaining <= 0:
                        return
                cursor = data.get("next_cursor")
                if not cursor:
                    return

    async def split_exists(self) -> bool:
        async with httpx.AsyncClient(base_url=self.service_url.rstrip("/"), timeout=self.timeout_seconds) as client:
            response = await client.post(
                "/v1/cases/query",
                json={
                    "dataset": self.dataset,
                    "domain": self.domain,
                    "split": self.split,
                    "cursor": None,
                    "limit": 1,
                    "filters": self.filters,
                },
            )
            response.raise_for_status()
            return bool(response.json().get("cases"))


@dataclass(slots=True)
class RemoteRolloutExecutor:
    """Execute rollouts through a benchmark/environment HTTP service."""

    service_url: str
    options: dict[str, Any] = field(default_factory=dict)
    concurrency: int = 20
    request_timeout_seconds: float = 300.0
    poll_interval_seconds: float = 2.0
    execution_timeout_seconds: float = 3600.0
    missing_execution_grace_seconds: float = 60.0
    show_progress: bool = False
    progress_label: str = "rollout"
    on_rollout_complete: Any | None = None

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be > 0")
        if self.missing_execution_grace_seconds <= 0:
            raise ValueError("missing_execution_grace_seconds must be > 0")

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        case_list = list(cases)
        stage_label = _progress_stage_label(
            context.metadata.get("stage"),
            default=self.progress_label,
        )
        timeout = httpx.Timeout(self.request_timeout_seconds)
        async with httpx.AsyncClient(base_url=self.service_url.rstrip("/"), timeout=timeout) as client:

            async def _execute(case: Case, index: int) -> Rollout:
                rollout = await self._execute_one(client, case, policy_set, context)
                await self._emit_rollout_complete(
                    rollout=rollout,
                    index=index,
                    context=context,
                )
                return rollout

            return await run_with_progress(
                case_list,
                coroutine_factory=_execute,
                label=stage_label,
                enabled=self.show_progress,
                description=f"Running {len(case_list)} rollouts, concurrency={self.concurrency}",
                concurrency=self.concurrency,
            )

    async def _emit_rollout_complete(
        self,
        *,
        rollout: Rollout,
        index: int,
        context: ExecutionContext,
    ) -> None:
        if self.on_rollout_complete is None:
            return
        result = self.on_rollout_complete(
            rollout=rollout,
            index=index,
            context=context,
        )
        if inspect.isawaitable(result):
            await result

    async def _execute_one(
        self,
        client: httpx.AsyncClient,
        case: Case,
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> Rollout:
        response = await client.post(
            "/v1/rollouts/execute",
            json={
                "case": case_to_dict(case),
                "policy_set": policy_set_to_dict(policy_set),
                "execution_context": {
                    "policy_snapshot_id": context.policy_snapshot_id,
                    "metadata": context.metadata,
                },
                "options": _remote_execution_options(self.options),
            },
        )
        response.raise_for_status()
        execution_id = _require_execution_id(response.json(), case=case)
        return await self._poll_execution(client, execution_id, case=case)

    async def _poll_execution(
        self,
        client: httpx.AsyncClient,
        execution_id: str,
        *,
        case: Case,
    ) -> Rollout:
        loop = asyncio.get_running_loop()
        started_at = loop.time()
        deadline = started_at + self.execution_timeout_seconds
        missing_execution_deadline = started_at + self.missing_execution_grace_seconds
        transient_errors = 0
        missing_execution_errors = 0
        last_transient_error: BaseException | None = None
        last_transient_response: httpx.Response | None = None
        while True:
            try:
                response = await client.get(f"/v1/rollouts/executions/{execution_id}")
                transient_errors = 0
            except (httpx.ReadError, httpx.ConnectError, httpx.RemoteProtocolError, httpx.TimeoutException) as exc:
                transient_errors += 1
                last_transient_error = exc
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        f"rollout execution {execution_id} polling timed out for case {case.name} "
                        f"after {self.execution_timeout_seconds}s; last polling error: "
                        f"{type(exc).__name__}: {exc}"
                    ) from exc
                await asyncio.sleep(min(self.poll_interval_seconds * transient_errors, 10.0))
                continue
            if response.status_code == 404:
                missing_execution_errors += 1
                if (
                    loop.time() >= deadline
                    or loop.time() >= missing_execution_deadline
                ):
                    raise RuntimeError(
                        f"rollout execution {execution_id} was not found while polling "
                        f"case {case.name}; observed {missing_execution_errors} 404 response(s) "
                        f"over {loop.time() - started_at:.1f}s. Last response: "
                        f"{_response_text(response)}"
                    )
                await asyncio.sleep(
                    min(self.poll_interval_seconds * missing_execution_errors, 10.0)
                )
                continue
            if _is_retryable_poll_response(response):
                transient_errors += 1
                last_transient_response = response
                if loop.time() >= deadline:
                    raise TimeoutError(
                        f"rollout execution {execution_id} polling timed out for case {case.name} "
                        f"after {self.execution_timeout_seconds}s; last polling response: "
                        f"{response.status_code} {_response_text(response)}"
                    )
                await asyncio.sleep(min(self.poll_interval_seconds * transient_errors, 10.0))
                continue
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "completed":
                rollout_data = data.get("rollout")
                if not isinstance(rollout_data, dict):
                    raise RuntimeError(f"rollout execution {execution_id} completed without rollout")
                return rollout_from_dict(rollout_data)
            if status == "failed":
                raise RuntimeError(
                    f"rollout execution {execution_id} failed for case {case.name}: "
                    f"{data.get('error') or 'unknown error'}"
                )
            if asyncio.get_running_loop().time() >= deadline:
                last_error_text = (
                    f"; last polling error: {type(last_transient_error).__name__}: "
                    f"{last_transient_error}"
                    if last_transient_error is not None
                    else (
                        f"; last polling response: {last_transient_response.status_code} "
                        f"{_response_text(last_transient_response)}"
                        if last_transient_response is not None
                        else ""
                    )
                )
                raise TimeoutError(
                    f"rollout execution {execution_id} timed out for case {case.name} "
                    f"after {self.execution_timeout_seconds}s{last_error_text}"
                )
            await asyncio.sleep(self.poll_interval_seconds)


def _progress_stage_label(stage: Any, *, default: str) -> str:
    stage_text = str(stage or "")
    stage_parts = stage_text.split(maxsplit=1)
    stage_name = stage_parts[0] if stage_parts else ""
    if _is_progress_stage_name(stage_name):
        return f"{stage_name}_start"
    if stage_name.endswith("_start") and _is_progress_stage_name(stage_name[:-6]):
        return stage_name
    return default


def _is_progress_stage_name(stage_name: str) -> bool:
    return (
        stage_name == "train_rollout"
        or stage_name == "test_rollout"
        or stage_name.endswith("_rollout")
    )


def _remote_execution_options(options: dict[str, Any]) -> dict[str, Any]:
    execution_options = dict(options)
    execution_options.pop("concurrency", None)
    return execution_options


def _require_execution_id(data: dict[str, Any], *, case: Case) -> str:
    execution_id = data.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise RuntimeError(f"rollout service did not return execution_id for case {case.name}")
    return execution_id


def _is_retryable_poll_response(response: httpx.Response) -> bool:
    return response.status_code == 408 or response.status_code == 429 or response.status_code >= 500


def _response_text(response: httpx.Response, *, max_chars: int = 500) -> str:
    try:
        text = response.text
    except Exception:
        return "<unavailable>"
    text = text.replace("\n", "\\n")
    if len(text) > max_chars:
        return text[:max_chars] + "..."
    return text
