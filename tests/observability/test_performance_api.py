"""Actual console router, canonical Task ownership and cross-thread stage context."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from openviking.observability.events import ObservabilityEvent, build_event, get_event_bus
from openviking.observability.performance import configure_performance, performance_stage
from openviking.observability.usage_audit.api_service import UsageAuditQueryService
from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.routers.console import router
from openviking.service.task_work_index import bind_task_context
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_http_role_scope_frozen_list_detail_and_date_validation(usage_store_factory):
    store = usage_store_factory(performance_enabled=True)
    await store.initialize()
    now = datetime.now(timezone.utc)
    await store.record_batch(
        [
            ObservabilityEvent(
                "vlm.call", {"task_id": "t", "prompt_tokens": n}, account_id=a, user_id=u
            )
            for a, u, n in [("a", "alice", 1), ("a", "bob", 2), ("b", "alice", 9)]
        ]
    )
    service = UsageAuditQueryService(store=store, inventory=object())
    app = FastAPI()
    app.include_router(router)
    app.state.usage_audit_runtime = SimpleNamespace(api_service=service)
    identity = {"role": Role.USER}
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "alice"), role=identity["role"]
    )
    params = {
        "start": (now - timedelta(hours=1)).isoformat(),
        "end": (now + timedelta(hours=1)).isoformat(),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/console/performance", params={**params, "view": "workflows"}
        )
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert len(result["items"]) == 1
        assert result["summary"]["token_totals"]["input_tokens"]["known_tokens"] == 1
        response = await client.get(
            "/api/v1/console/performance",
            params={
                **params,
                "view": "details",
                "workflow_id": "t",
                "observed_before": result["observed_before"],
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["result"]["summary"]["observations"] == 1
        # Query service rejects cross-user filters before reaching SQL.
        with pytest.raises(PermissionDeniedError) as denied:
            await service.performance(
                RequestContext(user=UserIdentifier("a", "alice"), role=Role.USER),
                user_id="bob",
                **params,
            )
        assert "another user" in str(denied.value)
        identity["role"] = Role.ADMIN
        response = await client.get(
            "/api/v1/console/performance", params={**params, "view": "workflows"}
        )
        assert len(response.json()["result"]["items"]) == 2
        invalid = await client.get(
            "/api/v1/console/performance", params={**params, "page_size": 101}
        )
        assert invalid.status_code == 422
    from openviking_cli.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError):
        await service.performance(
            RequestContext(user=UserIdentifier("a", "alice"), role=Role.USER),
            start="2026-01-01",
            end="2026-01-02",
        )


@pytest.mark.asyncio
async def test_stage_context_tracks_existing_work_and_does_not_bleed_principals():
    configure_performance(True)
    captured = []
    bus = get_event_bus()
    bus.register("stage-test", captured.append)
    try:
        with bind_task_context("t", "a", "alice", work_id="w", task_type="compile"):
            with performance_stage("compile.map"):
                call = await asyncio.to_thread(build_event, "vlm.call", {"prompt_tokens": 3})
                assert (
                    call.account_id,
                    call.user_id,
                    call.payload["task_id"],
                    call.payload["work_id"],
                ) == ("a", "alice", "t", "w")
                assert call.payload["stage_id"]
                # Shared work explicitly suppresses a timer's first-task stage.
                batch = build_event("vlm.call", {"task_id": "", "source_task_ids": ["x", "y"]})
                assert not batch.payload.get("stage_id")
                with bind_task_context("other", "b", "bob"):
                    other = build_event("vlm.call", {})
                    assert not other.payload.get("stage_id")
        stages = [e for e in captured if e.event_name == "workflow.stage"]
        assert len(stages) == 1 and stages[0].payload["stage_id"] == call.payload["stage_id"]
        assert stages[0].payload["duration_seconds"] >= 0
    finally:
        bus.unregister("stage-test")
        configure_performance(False)


@pytest.mark.asyncio
async def test_disabled_route_is_additive_not_a_missing_endpoint():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier("a", "alice"), role=Role.USER
    )
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get(
            "/api/v1/console/performance",
            params={"start": (now - timedelta(hours=1)).isoformat(), "end": now.isoformat()},
        )
        assert response.status_code == 200
        assert response.json()["result"]["enabled"] is False


@pytest.mark.asyncio
async def test_bot_gateway_bootstrap_reuses_explicit_server_projection_configuration(
    tmp_path, monkeypatch
):
    import openviking.observability.usage_audit.runtime as runtime
    import openviking.server.config as config_module
    from openviking.server.config import ObservabilityConfig, ServerConfig, UsageAuditConfig

    path = tmp_path / "synthetic.conf"
    configs = {"enabled": False}

    def load(candidate):
        assert candidate == str(path)
        return ServerConfig(
            observability=ObservabilityConfig(
                usage_audit=UsageAuditConfig(
                    performance_enabled=configs["enabled"], sqlite_path=str(tmp_path / "stats.db")
                )
            )
        )

    monkeypatch.setattr(config_module, "load_server_config", load)
    import openviking_cli.utils.config.config_loader as config_loader

    monkeypatch.setattr(
        config_loader,
        "load_json_config",
        lambda path: {
            "server": {
                "observability": {"usage_audit": {"performance_enabled": configs["enabled"]}}
            }
        },
    )
    app = SimpleNamespace(state=SimpleNamespace())
    assert await runtime.init_bot_usage_audit(path, app=app) is None
    configs["enabled"] = True
    started = await runtime.init_bot_usage_audit(path, app=app)
    assert started is not None
    try:
        with bind_task_context("cmp_shared", "a", "alice", task_type="compile"):
            with performance_stage("compile.gateway"):
                get_event_bus().publish(build_event("vlm.call", {"prompt_tokens": 5}))
        await started.worker.flush()
        now = datetime.now(timezone.utc)
        result = await started.store.query_performance(
            account_id="a",
            user_id="alice",
            start=(now - timedelta(hours=1)).isoformat(),
            end=(now + timedelta(seconds=1)).isoformat(),
            view="workflows",
        )
        assert result["summary"]["observed_model_events"] == 1
        assert {row["workflow_id"] for row in result["items"]} == {"cmp_shared"}
    finally:
        await runtime.shutdown_usage_audit(app=app)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, expected", [("dev", ("default", "default")), ("api_key", ("a", "alice"))]
)
async def test_runtime_compile_entry_binds_resolved_owner_and_closes_stage(mode, expected):
    from unittest.mock import AsyncMock

    from vikingbot.compile.service import BotCompileService

    service = object.__new__(BotCompileService)
    lock = asyncio.Lock()
    service._openviking_auth_mode = lambda: mode
    service._retain_target_lock = AsyncMock(return_value=lock)
    service._release_target_lock = AsyncMock()
    service._semaphore = asyncio.Semaphore(1)

    async def acquire(target_lock):
        await service._semaphore.acquire()
        await target_lock.acquire()

    service._acquire_execution_slot = acquire
    events = []
    bus = get_event_bus()
    bus.register("runtime-owner-test", events.append)
    configure_performance(True)

    async def execute(task_id, request, connection):
        # Exercise the actual Runtime entry, not a test-created task scope.
        event = await asyncio.to_thread(build_event, "vlm.call", {"prompt_tokens": 2})
        assert (event.account_id, event.user_id) == expected
        assert event.payload["task_id"] == "cmp_entry"
        assert event.payload["stage"] == "compile.execution"
        bus.publish(event)

    service._execute_task = execute
    try:
        await service._run_task(
            "cmp_entry",
            SimpleNamespace(to="viking://resources/generated"),
            {"account_id": "a", "user_id": "alice"},
        )
        stages = [event for event in events if event.event_name == "workflow.stage"]
        assert len(stages) == 1 and stages[0].payload["status"] == "success"
        assert not lock.locked() and service._semaphore._value == 1
        assert not build_event("vlm.call", {}).payload.get("stage_id")
    finally:
        bus.unregister("runtime-owner-test")
        configure_performance(False)
