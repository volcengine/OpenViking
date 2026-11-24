from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

import openviking.server.routers.rules as rules_router
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.service.rule_service import RuleCompilePlan
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_api_resources import _wait_task_terminal


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
async def test_rule_compile_route_runs_with_real_task_tracker(client, app, service, monkeypatch):
    app.state.config.temp_upload.default_mode = "shared"
    content = "# Review guidelines\n\nConfirm before deleting files."
    uploaded = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("guidelines.md", content, "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    upload_id = uploaded.json()["result"]["temp_file_id"]
    assert upload_id.startswith("shared_")

    async def plan_from_source(*, source_text, **kwargs):
        assert content in source_text
        return RuleCompilePlan(reason="Reviewed the uploaded guidelines.")

    monkeypatch.setattr(service.rules, "_compiler_vlm", AsyncMock(return_value=SimpleNamespace()))
    monkeypatch.setattr(service.rules, "_compiler_prompt_token_limit", lambda _: 8_000)
    monkeypatch.setattr(service.rules, "_build_compile_plan", plan_from_source)

    response = await client.post(
        "/api/v1/rules/compile",
        json={"sources": [{"temp_file_id": upload_id, "wait": False, "internal_task": False}]},
    )
    assert response.status_code == 202, response.text
    task_id = response.json()["result"]["task_id"]
    task = await _wait_task_terminal(client, task_id)
    assert task["status"] == "completed", task
    assert task["stage"] == "completed"
    assert task["result"]["plan_reason"] == "Reviewed the uploaded guidelines."
    [root] = task["result"]["source_uris"]
    read = await client.get("/api/v1/content/read", params={"uri": f"{root}/guidelines.md"})
    assert read.status_code == 200, read.text
    assert read.json()["result"].strip() == content

    tasks = {"task_type": "add_resource", "resource_id": root}
    visible = await client.get("/api/v1/tasks", params=tasks)
    assert visible.json()["result"] == []
    internal = await client.get("/api/v1/tasks", params={**tasks, "include_internal": True})
    assert [item["status"] for item in internal.json()["result"]] == ["completed"]

    details = await client.get(f"/api/v1/tasks/{task_id}", params={"include_events": True})
    assert details.status_code == 200, details.text
    events = details.json()["result"]["execution_events"]["items"]
    assert [event["stage"] for event in events if event["kind"] == "stage_changed"] == [
        "queued",
        "ingesting_sources",
        "recalling_rules",
        "planning_operations",
        "writing_rules",
    ]
