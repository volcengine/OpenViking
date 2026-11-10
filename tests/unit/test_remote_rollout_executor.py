# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio

import httpx

from openviking.session.train.components.remote import RemoteRolloutExecutor
from openviking.session.train.context import ExecutionContext
from openviking.session.train.domain import (
    Case,
    Experience,
    ExperienceSet,
    Rubric,
    RubricCriterion,
)


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


def test_remote_rollout_executor_retries_transient_missing_execution(monkeypatch):
    calls: list[str] = []
    execution_id = "rollout_exec_delayed"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/v1/rollouts/execute":
            return httpx.Response(200, json={"execution_id": execution_id, "status": "running"})
        if request.method == "GET" and request.url.path.endswith(execution_id):
            get_count = sum(1 for call in calls if call.startswith("GET "))
            if get_count == 1:
                return httpx.Response(404, json={"detail": "not found yet"})
            return httpx.Response(
                200,
                json={
                    "execution_id": execution_id,
                    "status": "completed",
                    "rollout": {
                        "case": {
                            "name": "case-1",
                            "task_signature": "booking_duplicate",
                            "input": {"user_request": "cancel duplicate booking"},
                            "rubric": {
                                "name": "booking_rubric",
                                "description": "Cancel only the verified duplicate booking.",
                                "criteria": [
                                    {
                                        "name": "verify_duplicate",
                                        "description": "Verify duplicate status first.",
                                        "required": True,
                                        "weight": 1.0,
                                        "metadata": {},
                                    }
                                ],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        "messages": [],
                        "policy_snapshot_id": "snapshot-1",
                        "evaluation": None,
                        "metadata": {},
                    },
                },
            )
        return httpx.Response(500, json={"error": "unexpected request"})

    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_async_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url"),
            timeout=kwargs.get("timeout"),
        ),
    )

    executor = RemoteRolloutExecutor(
        service_url="http://rollout-service",
        poll_interval_seconds=0.01,
        missing_execution_grace_seconds=1.0,
    )

    rollouts = asyncio.run(
        executor.execute(
            [_case()],
            _policy_set(),
            ExecutionContext(policy_snapshot_id="snapshot-1"),
        )
    )

    assert len(rollouts) == 1
    assert rollouts[0].case.name == "case-1"
    assert calls.count(f"GET /v1/rollouts/executions/{execution_id}") == 2


def test_remote_rollout_executor_retries_transient_poll_5xx(monkeypatch):
    calls: list[str] = []
    execution_id = "rollout_exec_gateway_blip"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST" and request.url.path == "/v1/rollouts/execute":
            return httpx.Response(200, json={"execution_id": execution_id, "status": "running"})
        if request.method == "GET" and request.url.path.endswith(execution_id):
            get_count = sum(1 for call in calls if call.startswith("GET "))
            if get_count == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(
                200,
                json={
                    "execution_id": execution_id,
                    "status": "completed",
                    "rollout": {
                        "case": {
                            "name": "case-1",
                            "task_signature": "booking_duplicate",
                            "input": {"user_request": "cancel duplicate booking"},
                            "rubric": {
                                "name": "booking_rubric",
                                "description": "Cancel only the verified duplicate booking.",
                                "criteria": [
                                    {
                                        "name": "verify_duplicate",
                                        "description": "Verify duplicate status first.",
                                        "required": True,
                                        "weight": 1.0,
                                        "metadata": {},
                                    }
                                ],
                                "metadata": {},
                            },
                            "metadata": {},
                        },
                        "messages": [],
                        "policy_snapshot_id": "snapshot-1",
                        "evaluation": None,
                        "metadata": {},
                    },
                },
            )
        return httpx.Response(500, json={"error": "unexpected request"})

    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: original_async_client(
            transport=httpx.MockTransport(handler),
            base_url=kwargs.get("base_url"),
            timeout=kwargs.get("timeout"),
        ),
    )

    executor = RemoteRolloutExecutor(
        service_url="http://rollout-service",
        poll_interval_seconds=0.01,
        execution_timeout_seconds=1.0,
    )

    rollouts = asyncio.run(
        executor.execute(
            [_case()],
            _policy_set(),
            ExecutionContext(policy_snapshot_id="snapshot-1"),
        )
    )

    assert len(rollouts) == 1
    assert rollouts[0].case.name == "case-1"
    assert calls.count(f"GET /v1/rollouts/executions/{execution_id}") == 2
