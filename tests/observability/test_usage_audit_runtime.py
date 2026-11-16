# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from openviking.observability.events import _GLOBAL_EVENT_BUS, try_publish_event
from openviking.observability.http_observability_middleware import HTTPObservabilityMiddleware
from openviking.observability.usage_audit import (
    init_usage_audit_from_server_config,
    shutdown_usage_audit,
)
from openviking.retrieve.context_assembler.models import AssembledEntry, AssembleResult
from openviking.server.auth import get_request_context
from openviking.server.config import ObservabilityConfig, ServerConfig, UsageAuditConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.request_id import RequestIdMiddleware
from openviking.server.routers import search as search_router
from openviking_cli.retrieve import ContextType, FindResult, MatchedContext
from openviking_cli.session.user_id import UserIdentifier


@pytest.mark.asyncio
async def test_usage_audit_runtime_subscribes_to_shared_event_bus(tmp_path):
    _GLOBAL_EVENT_BUS.clear()
    app = SimpleNamespace(state=SimpleNamespace())
    config = ServerConfig(
        observability=ObservabilityConfig(
            usage_audit=UsageAuditConfig(
                sqlite_path=str(tmp_path / "usage.sqlite3"),
                flush_interval_seconds=0.1,
                timezone="UTC",
            )
        )
    )
    runtime = await init_usage_audit_from_server_config(config, app=app, service=object())
    assert runtime is not None
    try:
        try_publish_event(
            "http.request",
            {
                "request_id": "req-runtime",
                "account_id": "acct-runtime",
                "user_id": "user-runtime",
                "method": "POST",
                "route": "/api/v1/search/find",
                "status": "200",
                "duration_seconds": 0.01,
                "error_code": "SHOULD_NOT_PERSIST",
                "error_message": "success response",
            },
        )
        await runtime.worker.close(timeout_seconds=1.0)

        from zoneinfo import ZoneInfo

        retrievals = await runtime.store.get_today_retrievals(
            account_id="acct-runtime",
            user_date=runtime.api_service.today(),
            tz=ZoneInfo("UTC"),
        )
        audit = await runtime.store.query_audit_logs(account_id="acct-runtime")
    finally:
        await shutdown_usage_audit(app=app)
        _GLOBAL_EVENT_BUS.clear()

    assert retrievals["find"] == 1
    assert audit["total"] == 1
    assert audit["items"][0]["request_id"] == "req-runtime"
    assert audit["items"][0]["error_code"] is None
    assert audit["items"][0]["error_message"] is None
    assert audit["items"][0]["error_details"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,mode", [("find", "list"), ("search", "list"), ("search", "context")]
)
async def test_search_http_results_persist_without_counting_internal_queries(
    tmp_path, monkeypatch, operation, mode
):
    async def retrieve(query):
        await asyncio.sleep(0)
        # Internal candidates must not inflate the final HTTP result count.
        try_publish_event("retrieval.completed", {"result_count": 99})
        # A worker batch may finish before the corresponding HTTP request.
        assert runtime is not None
        await runtime.worker.flush()
        if query == "error":
            raise HTTPException(status_code=503, detail="retrieval unavailable")
        return (
            []
            if query == "empty"
            else [
                MatchedContext(uri=f"viking://resources/{i}.md", context_type=ContextType.RESOURCE)
                for i in range(3)
            ]
        )

    async def find_or_search(query, **kwargs):
        return FindResult(memories=[], resources=await retrieve(query), skills=[])

    async def assemble(*, params, **kwargs):
        hits = await retrieve(params.query)
        return AssembleResult(
            entries=[
                AssembledEntry(uri=hit.uri, category="resources", score=0.8, detail="abstract")
                for hit in hits
            ]
        )

    service = SimpleNamespace(search=SimpleNamespace(find=find_or_search, search=find_or_search))
    monkeypatch.setattr(search_router, "get_service", lambda: service)
    monkeypatch.setattr(search_router, "assemble_context", assemble)
    app = FastAPI()
    app.include_router(search_router.router)
    app.add_middleware(HTTPObservabilityMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.dependency_overrides[get_request_context] = lambda: RequestContext(
        user=UserIdentifier.the_default_user(), role=Role(Role.ROOT)
    )
    db_path = tmp_path / "usage.sqlite3"
    runtime = await init_usage_audit_from_server_config(
        ServerConfig.model_validate(
            {"observability": {"usage_audit": {"sqlite_path": str(db_path)}}}
        ),
        app=app,
        service=service,
    )
    assert runtime is not None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            responses = await asyncio.gather(
                *[
                    client.post(
                        f"/api/v1/search/{operation}",
                        json={"query": query, **({"mode": mode} if operation == "search" else {})},
                        headers={"X-Request-ID": "shared-client-id"},
                    )
                    for query in ("full", "empty", "error")
                ]
            )
            await runtime.worker.flush()
            repeated = await client.post(
                f"/api/v1/search/{operation}",
                json={"query": "full", **({"mode": mode} if operation == "search" else {})},
                headers={"X-Request-ID": "shared-client-id"},
            )
            assert repeated.status_code == 200
        assert [response.status_code for response in responses] == [200, 200, 503]
        result = responses[0].json()["result"]
        assert (len(result["entries"]) if mode == "context" else result["total"]) == 3
        await runtime.worker.flush()
    finally:
        await shutdown_usage_audit(app=app)

    # Read the persisted public projection after shutdown, not just its event payload.
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(
            "SELECT operation, status, SUM(request_count), SUM(result_count) "
            "FROM usage_retrieval_hourly GROUP BY operation, status ORDER BY status"
        ).fetchall()
    assert rows == [(operation, "error", 1, 0), (operation, "success", 3, 6)]
