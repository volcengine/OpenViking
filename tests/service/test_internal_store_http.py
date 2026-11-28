# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Exercise the internal store wire contract at the HTTP boundary."""

import json
from contextlib import aclosing

import httpx
import pytest

from openviking.service.compile_store_sqlite import SQLiteCompileStore
from openviking.service.compile_targets import new_record
from openviking.storage.internal_http import InternalServiceError
from openviking.storage.task_service.compile import ListConfigsRequest
from openviking.storage.task_service.openapi import build_openapi
from openviking_cli.utils.config.storage_config import StorageConfig


@pytest.fixture
def stores(tmp_path):
    storage = StorageConfig(
        workspace=str(tmp_path),
        task_service={
            "base_url": "https://task.internal",
            "token": "task-token",
            "actor_peer_id": "peer",
        },
        task_store={"provider": "http"},
        watch_store={"provider": "http"},
        compile_store={"provider": "http"},
    )
    return (
        storage.build_task_tracker(None, service_name="irrelevant")._store,
        storage.build_watch_store(service_name="irrelevant", viking_fs=None),
        storage.build_compile_store("irrelevant"),
    )


async def test_task_service_uses_configured_token_without_discovery(stores):
    task, watch, compile_store = stores
    calls = []

    def handle(request):
        path = request.url.path
        body = json.loads(request.content)
        calls.append((request.url.host, path, body))
        assert request.method == "POST"
        assert "x-api-key" not in request.headers
        assert "service_name" not in body and "params" not in body
        assert request.url.host == "task.internal"
        assert request.headers["authorization"] == "Bearer task-token"
        assert request.headers["x-openviking-actor-peer"] == "peer"
        result = {
            "/api/v1/inner/tasks/get": None,
            "/api/v1/inner/tasks/watch/list-due": [],
            "/api/v1/inner/tasks/compile/runs/update": False,
            "/api/v1/inner/tasks/compile/configs/list": [["a", "c", {"owner_user_id": "u"}]],
            "/api/v1/inner/tasks/compile/configs/cleanup": None,
        }[path]
        return httpx.Response(200, json={"result": result})

    for client in (task._service, watch._service, compile_store._http):
        client._transport = httpx.MockTransport(handle)
    try:
        assert await task.get_task("task", account_id="a", user_id="u") is None
        assert calls == [
            (
                "task.internal",
                "/api/v1/inner/tasks/get",
                {"task_id": "task", "account_id": "a", "user_id": "u"},
            ),
        ]
        await task.load()
        await watch.initialize()
        await compile_store.load()
        assert len(calls) == 1
        assert await task.get_task("task") is None
        client = task._service._client
        assert client is not None
        assert await task.get_task("task") is None
        assert task._service._client is client
        assert await watch.due_candidates(10, offset=3) == []
        assert calls[-1][2] == {"limit": 10, "offset": 3}
        assert not await compile_store.update_run(
            "a", "u", "c", "task", {"status": "done"}, expected={"status": "running"}, replace=True
        )
        assert calls[-1][2]["replace"] is True
        assert calls[-1][2]["expected"] == {"status": "running"}
        assert [row async for row in compile_store.list_configs("a", "u", fresh=True)] == [
            ("a", "c", {"owner_user_id": "u"})
        ]
        await compile_store.delete_config("a", "u")
        assert calls[-1][2] == {"account_id": "a", "owner_user_id": "u"}
    finally:
        for store in stores:
            await store.close()
    assert client.is_closed


async def test_rejects_service_token_without_retry_or_logging_remote_body(stores):
    store = stores[0]
    calls = []

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(
            401, json={"error": {"code": "invalid_service_token", "message": "task-token"}}
        )

    store._service._transport = httpx.MockTransport(handle)
    try:
        with pytest.raises(InternalServiceError) as caught:
            await store.get_task("task")
    finally:
        await store.close()
    assert caught.value.status_code == 401
    assert "task-token" not in str(caught.value)
    assert calls == ["/api/v1/inner/tasks/get"]


def test_contract_has_unique_paths_and_requires_bearer_token():
    schema = build_openapi()
    assert len(schema["paths"]) == 55
    assert all(path.startswith("/api/v1/inner/tasks/") for path in schema["paths"])
    assert schema["security"] == [{"LibraryToken": []}]
    assert set(schema["paths"]["/api/v1/inner/tasks/get"]) == {"post"}
    assert schema["components"]["securitySchemes"]["LibraryToken"] == {
        "type": "http",
        "scheme": "bearer",
    }


async def test_compile_schedule_scan_pages_enabled_configs_across_accounts(stores, tmp_path):
    store = stores[2]
    backend = SQLiteCompileStore({"path": str(tmp_path / "compile.db")}, "test")
    await backend.load()
    size = 128
    requests = []

    async def handle(request):
        assert request.url.path == "/api/v1/inner/tasks/compile/configs/list"
        args = ListConfigsRequest.model_validate_json(request.content)
        requests.append(args)
        rows = [row async for row in backend.list_configs(**args.model_dump())]
        return httpx.Response(200, json={"result": rows})

    store._http._transport = httpx.MockTransport(handle)
    await store.load()
    try:
        owner = "owner"
        base = new_record(
            {"from": [], "to": "viking://resources/dst", "skill": "memory"},
            {"owner_user_id": owner},
        )
        enabled = {
            **base,
            "auto_update": True,
            "next_triggered_slot": "2027-01-01T00:00:00+08:00",
            "update_schedule": {"frequency": "daily", "hour": 0, "minute": 0},
        }
        await backend.create_config("a", owner, "manual", base)
        names = [f"config{i:03}" for i in range(size)]
        for name in names:
            await backend.create_config("a", owner, name, enabled)
        # The same name and owner still has a distinct position in another account.
        await backend.create_config("b", owner, names[0], enabled)

        async with aclosing(store.list_configs(auto_update=True)) as scan:
            first_page = [await anext(scan) for _ in range(size)]
            assert len(requests) == 1
            assert [(account, name) for account, name, _ in first_page] == [
                ("a", name) for name in names
            ]
            assert first_page[0][2]["update_schedule"] == enabled["update_schedule"]
            assert [(account, name) async for account, name, _ in scan] == [("b", names[0])]
        assert [(r.auto_update, r.limit, r.after_key) for r in requests] == [
            (True, size, None),
            (True, size, ("a", names[-1], owner)),
        ]
        assert [name async for _, name, _ in store.list_configs(auto_update=False)] == ["manual"]
    finally:
        await store.close()
        await backend.close()
