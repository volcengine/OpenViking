import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.rules as rules_router
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.rule_service import RuleCompilePlan, RuleService
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskStatus, TaskTracker, set_task_tracker
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
async def test_rule_compile_and_search_http_contract(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    task = SimpleNamespace(
        to_dict=lambda: {
            "task_id": "rul_123",
            "task_type": "rule_compile",
            "status": "pending",
        }
    )
    rules = SimpleNamespace(
        create_compile=AsyncMock(return_value=task),
        search_rules=AsyncMock(
            return_value={
                "rules": [
                    {
                        "atomic_id": "confirm-delete",
                        "content": "# Complete rule",
                        "probability": 0.91,
                    }
                ],
                "judge": "jev_noul",
            }
        ),
    )
    monkeypatch.setattr(
        rules_router,
        "get_service",
        lambda: SimpleNamespace(rules=rules),
    )
    app = FastAPI()
    app.include_router(rules_router.router)
    app.dependency_overrides[get_request_context] = lambda: ctx

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        compile_response = await client.post(
            "/api/v1/rules/compile",
            json={"instruction": "Confirm before deleting files."},
        )
        search_response = await client.post(
            "/api/v1/rules/search",
            json={"task_context": "Delete a generated file."},
        )

    assert compile_response.status_code == 202
    assert compile_response.json()["result"]["task_id"] == "rul_123"
    assert search_response.status_code == 200
    assert search_response.json()["result"]["rules"][0]["atomic_id"] == "confirm-delete"
    rules.create_compile.assert_awaited_once()
    rules.search_rules.assert_awaited_once()


@pytest.mark.asyncio
async def test_rule_compile_route_runs_with_real_task_tracker(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    tracker = TaskTracker(PersistentTaskStore(_FakeAgfs()))
    service = RuleService()
    service.set_dependencies(
        fs=SimpleNamespace(),
        resources=SimpleNamespace(),
        search=SimpleNamespace(),
        vlm_resolver=SimpleNamespace(),
    )
    monkeypatch.setattr(service, "_ensure_rule_directories", AsyncMock())
    monkeypatch.setattr(service, "_collect_sources", AsyncMock(return_value=("", [])))
    monkeypatch.setattr(service, "_compiler_vlm", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service, "_compiler_prompt_token_limit", lambda _: 8_000)
    monkeypatch.setattr(
        service,
        "_find_relevant_rules",
        AsyncMock(return_value=SimpleNamespace(scenarios=[], selected=[])),
    )
    monkeypatch.setattr(
        service,
        "_build_compile_plan",
        AsyncMock(return_value=RuleCompilePlan()),
    )
    monkeypatch.setattr(
        rules_router,
        "get_service",
        lambda: SimpleNamespace(rules=service),
    )
    app = FastAPI()
    app.include_router(rules_router.router)
    app.dependency_overrides[get_request_context] = lambda: ctx
    set_task_tracker(tracker)

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/api/v1/rules/compile",
                json={"instruction": "Confirm before deleting files."},
            )

        assert response.status_code == 202
        task_id = response.json()["result"]["task_id"]
        while service._running:
            await asyncio.gather(*tuple(service._running))

        task = await tracker.get_with_events(task_id, account_id="acme", user_id="alice")
        assert task is not None
        assert task.status is TaskStatus.COMPLETED
        assert task.stage == "completed"
        assert task.execution_events is not None
        assert [
            event["stage"]
            for event in task.execution_events["items"]
            if event["kind"] == "stage_changed"
        ] == [
            "queued",
            "ingesting_sources",
            "recalling_rules",
            "planning_operations",
            "writing_rules",
        ]
        assert not service._running
    finally:
        for background in tuple(service._running):
            background.cancel()
        if service._running:
            await asyncio.gather(*tuple(service._running), return_exceptions=True)
        set_task_tracker(None)
