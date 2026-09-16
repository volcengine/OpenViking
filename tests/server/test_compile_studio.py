"""Studio task pagination and submission retry contracts."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import ExternalTaskService
from openviking.service.task_pagination import decode_cursor, encode_cursor, page_scope
from openviking.service.task_store import PersistentTaskStore
from openviking.service.task_tracker import TaskTracker
from openviking_cli.exceptions import ConflictError, InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier
from tests.test_task_tracker import _FakeAgfs


@pytest.mark.asyncio
async def test_page_reads_past_200_and_isolates_owner():
    store = PersistentTaskStore(_FakeAgfs())
    tracker = TaskTracker(store)
    owner = {"account_id": "acme", "user_id": "alice"}
    for index in range(205):
        record = await tracker.create(
            "compile",
            task_id=f"cmp_{index:03}",
            meta={"request": {"to": "viking://resources/wiki"}},
            **owner,
        )
        record.created_at = 100.0
        await store.update(record)
    await tracker.create("compile", task_id="foreign", account_id="acme", user_id="bob")
    # Empty cache: pagination must read durable records without repopulating all tasks.
    tracker = TaskTracker(store)
    filters = {
        "task_type": "compile",
        "status": None,
        "resource_id": None,
        "include_internal": False,
        "q": "wiki",
    }
    seen = []
    before = None
    while True:
        page = await tracker.list_page(**owner, limit=30, before=before, **filters)
        if not page:
            break
        seen.extend(task.task_id for task in page)
        before = (page[-1].created_at, page[-1].task_id)
    assert len(seen) == len(set(seen)) == 205
    assert seen[0] == "cmp_204"
    assert "foreign" not in seen
    assert not tracker._tasks


def test_cursor_is_bound_to_identity_and_filters():
    scope = page_scope(account="a", user="u", status="running")
    cursor = encode_cursor({"created_at": 123.4, "task_id": "cmp_a"}, scope)
    assert decode_cursor(cursor, scope) == (123.4, "cmp_a")
    with pytest.raises(InvalidArgumentError):
        decode_cursor(cursor, page_scope(account="a", user="other", status="running"))
    with pytest.raises(InvalidArgumentError):
        decode_cursor("broken", scope)


@pytest.mark.asyncio
async def test_concurrent_submission_retry_and_restart(monkeypatch):
    import openviking.service.external_task_service as module

    store = PersistentTaskStore(_FakeAgfs())
    tracker = TaskTracker(store)
    monkeypatch.setattr(module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(module, "get_queue_manager", lambda: SimpleNamespace(enqueue=AsyncMock()))
    service = ExternalTaskService()
    service.register(SimpleNamespace(task_type="compile", task_id_prefix="cmp_"))
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    params = {
        "resource_id": None,
        "payload": {"from": ["viking://resources/a"], "to": "viking://resources/b"},
        "connection": {},
        "ctx": ctx,
        "idempotency_key": "test-submission-123",
    }
    results = await asyncio.gather(*(service.create("compile", **params) for _ in range(5)))
    assert len({t.task_id for t in results}) == 1
    tracker = TaskTracker(store)
    again = await service.create("compile", **params)
    assert again.task_id == results[0].task_id
    with pytest.raises(ConflictError):
        await service.create("compile", **{**params, "payload": {"to": "different"}})
    other = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)
    assert (await service.create("compile", **{**params, "ctx": other})).task_id != again.task_id


@pytest.mark.asyncio
async def test_http_contract_preserves_legacy_list_and_retries(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    import openviking.server.routers.compile as compile_router
    import openviking.server.routers.tasks as task_router
    import openviking.service.external_task_service as external
    import openviking.service.task_tracker as tracker_module
    from openviking.server.auth import get_request_context
    from openviking.service.compile_service import CompileService
    from openviking_cli.exceptions import OpenVikingError
    from openviking_cli.utils.config.open_viking_config import CompileApiConfig

    store = PersistentTaskStore(_FakeAgfs())
    tracker = TaskTracker(store)
    monkeypatch.setattr(external, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(task_router, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(tracker_module, "get_task_tracker", lambda: tracker)
    monkeypatch.setattr(external, "get_queue_manager", lambda: SimpleNamespace(enqueue=AsyncMock()))
    tasks = ExternalTaskService()
    fs = SimpleNamespace(
        stat=AsyncMock(
            side_effect=lambda uri, ctx: {"uri": uri, "isDir": not uri.endswith("SKILL.md")}
        ),
        ensure_write_access=AsyncMock(),
    )
    compile_service = CompileService(CompileApiConfig(), tasks, fs)
    compile_service.configure_local_backend("http://localhost:1", "")
    tasks.register(compile_service)
    monkeypatch.setattr(
        compile_router, "get_service", lambda: SimpleNamespace(compile=compile_service)
    )
    ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.USER)
    app = FastAPI()
    app.include_router(compile_router.router)
    app.include_router(task_router.router)
    app.dependency_overrides[get_request_context] = lambda: ctx

    @app.exception_handler(OpenVikingError)
    async def on_error(request, error):
        return JSONResponse(
            status_code=409 if error.code == "CONFLICT" else 400, content={"error": error.code}
        )

    payload = {
        "from": ["viking://resources/source"],
        "to": "viking://resources/wiki",
        "skill": "viking://agent/skills/wiki",
    }
    headers = {"Idempotency-Key": "http-retry-key-123"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.post("/api/v1/compile", json=payload, headers=headers)
        assert first.status_code == 202, first.text
        second = await client.post("/api/v1/compile", json=payload, headers=headers)
        task_id = first.json()["result"]["task_id"]
        assert second.json()["result"]["task_id"] == task_id
        recovery = await client.get("/api/v1/compile/submissions/http-retry-key-123")
        assert recovery.json()["result"]["task_id"] == task_id
        assert isinstance((await client.get("/api/v1/tasks")).json()["result"], list)
        paged = await client.get(
            "/api/v1/tasks", params={"pagination": "cursor", "task_type": "compile"}
        )
        assert paged.json()["result"]["items"][0]["task_id"] == task_id
        assert (await client.get("/api/v1/tasks", params={"limit": 0})).status_code == 422
        conflict = await client.post(
            "/api/v1/compile", json={**payload, "instruction": "changed"}, headers=headers
        )
        assert conflict.status_code == 409
        stopped = await client.post(f"/api/v1/tasks/{task_id}/cancel")
        assert stopped.status_code == 200
        assert stopped.json()["result"]["status"] in {"cancelled", "cancelling"}
        retry_after_cancel = await client.post("/api/v1/compile", json=payload, headers=headers)
        assert retry_after_cancel.json()["result"]["task_id"] == task_id
        assert "submission_hash" not in retry_after_cancel.json()["result"]["meta"]
        ctx = RequestContext(user=UserIdentifier("acme", "alice"), role=Role.ROOT)
        root_page = await client.get(
            "/api/v1/tasks", params={"pagination": "cursor", "task_type": "compile"}
        )
        assert root_page.json()["result"]["items"][0]["task_id"] == task_id
        ctx = RequestContext(user=UserIdentifier("acme", "bob"), role=Role.USER)
        assert (await client.get("/api/v1/tasks", params={"pagination": "cursor"})).json()[
            "result"
        ]["items"] == []
