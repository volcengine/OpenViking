# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP-backed CaseLoader and RolloutExecutor implementations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from openviking.message import Message
from openviking.session.train.components.progress import ProgressPrinter
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    CriterionResult,
    ExperienceSet,
    Rollout,
    Rubric,
    RubricCriterion,
    RubricEvaluation,
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
                cases = [_case_from_dict(item) for item in data.get("cases", [])]
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
    request_timeout_seconds: float = 60.0
    poll_interval_seconds: float = 2.0
    execution_timeout_seconds: float = 3600.0
    show_progress: bool = False
    progress_label: str = "rollout"

    def __post_init__(self) -> None:
        if self.concurrency <= 0:
            raise ValueError("concurrency must be > 0")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be > 0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.execution_timeout_seconds <= 0:
            raise ValueError("execution_timeout_seconds must be > 0")

    async def execute(
        self,
        cases: list[Case],
        policy_set: ExperienceSet,
        context: ExecutionContext,
    ) -> list[Rollout]:
        case_list = list(cases)
        progress = ProgressPrinter(
            total=len(case_list),
            label=self.progress_label,
            enabled=self.show_progress,
        )
        progress.render()
        semaphore = asyncio.Semaphore(self.concurrency)
        timeout = httpx.Timeout(self.request_timeout_seconds)
        async with httpx.AsyncClient(base_url=self.service_url.rstrip("/"), timeout=timeout) as client:

            async def execute_one(case: Case) -> Rollout:
                async with semaphore:
                    progress.start_one()
                    try:
                        return await self._execute_one(client, case, policy_set, context)
                    finally:
                        progress.complete_one()

            try:
                return list(await asyncio.gather(*(execute_one(case) for case in case_list)))
            finally:
                progress.finish()

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
                "case": _case_to_dict(case),
                "policy_set": _policy_set_to_dict(policy_set),
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
        deadline = asyncio.get_running_loop().time() + self.execution_timeout_seconds
        while True:
            response = await client.get(f"/v1/rollouts/executions/{execution_id}")
            response.raise_for_status()
            data = response.json()
            status = data.get("status")
            if status == "completed":
                rollout_data = data.get("rollout")
                if not isinstance(rollout_data, dict):
                    raise RuntimeError(f"rollout execution {execution_id} completed without rollout")
                return _rollout_from_dict(rollout_data)
            if status == "failed":
                raise RuntimeError(
                    f"rollout execution {execution_id} failed for case {case.name}: "
                    f"{data.get('error') or 'unknown error'}"
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(
                    f"rollout execution {execution_id} timed out for case {case.name} "
                    f"after {self.execution_timeout_seconds}s"
                )
            await asyncio.sleep(self.poll_interval_seconds)


def _remote_execution_options(options: dict[str, Any]) -> dict[str, Any]:
    execution_options = dict(options)
    execution_options.pop("concurrency", None)
    return execution_options


def _require_execution_id(data: dict[str, Any], *, case: Case) -> str:
    execution_id = data.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise RuntimeError(f"rollout service did not return execution_id for case {case.name}")
    return execution_id


def _policy_set_to_dict(policy_set: ExperienceSet) -> dict[str, Any]:
    return {
        "root_uri": policy_set.root_uri,
        "policies": [
            {
                "name": item.name,
                "uri": item.uri,
                "version": item.version,
                "status": item.status,
                "content": item.content,
                "metadata": item.metadata,
            }
            for item in policy_set.policies
        ],
        "metadata": policy_set.metadata,
    }


def _case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "name": case.name,
        "task_signature": case.task_signature,
        "input": case.input,
        "rubric": {
            "name": case.rubric.name,
            "description": case.rubric.description,
            "criteria": [
                {
                    "name": criterion.name,
                    "description": criterion.description,
                    "required": criterion.required,
                    "weight": criterion.weight,
                    "metadata": criterion.metadata,
                }
                for criterion in case.rubric.criteria
            ],
            "metadata": case.rubric.metadata,
        },
        "metadata": case.metadata,
    }


def _case_from_dict(data: dict[str, Any]) -> Case:
    rubric_data = data["rubric"]
    return Case(
        name=data["name"],
        task_signature=data["task_signature"],
        input=dict(data.get("input") or {}),
        rubric=Rubric(
            name=rubric_data["name"],
            description=rubric_data.get("description", ""),
            criteria=[
                RubricCriterion(
                    name=item["name"],
                    description=item.get("description", ""),
                    required=bool(item.get("required", True)),
                    weight=float(item.get("weight", 1.0)),
                    metadata=dict(item.get("metadata") or {}),
                )
                for item in rubric_data.get("criteria", [])
            ],
            metadata=dict(rubric_data.get("metadata") or {}),
        ),
        metadata=dict(data.get("metadata") or {}),
    )


def _rollout_from_dict(data: dict[str, Any]) -> Rollout:
    return Rollout(
        case=_case_from_dict(data["case"]),
        messages=[Message.from_dict(item) for item in data.get("messages", [])],
        policy_snapshot_id=data["policy_snapshot_id"],
        evaluation=_evaluation_from_dict(data.get("evaluation")),
        metadata=dict(data.get("metadata") or {}),
    )


def _evaluation_from_dict(data: dict[str, Any] | None) -> RubricEvaluation | None:
    if data is None:
        return None
    return RubricEvaluation(
        passed=bool(data.get("passed")),
        score=float(data.get("score") or 0.0),
        criterion_results=[
            CriterionResult(
                criterion_name=item.get("criterion_name", "unknown"),
                passed=bool(item.get("passed")),
                score=float(item.get("score") or 0.0),
                feedback=[str(value) for value in item.get("feedback", [])],
                evidence=[str(value) for value in item.get("evidence", [])],
                metadata=dict(item.get("metadata") or {}),
            )
            for item in data.get("criterion_results", [])
        ],
        feedback=[str(value) for value in data.get("feedback", [])],
        metadata=dict(data.get("metadata") or {}),
    )
