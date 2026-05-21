# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openviking.server.auth import get_request_context
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers.console import router as console_router
from openviking_cli.exceptions import InvalidArgumentError, OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


def _ctx(role: Role = Role.ADMIN) -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id="acct-1", user_id="user-1", agent_id="agent-1"),
        role=role,
    )


def _admin_ctx() -> RequestContext:
    return _ctx(Role.ADMIN)


def _user_ctx() -> RequestContext:
    return _ctx(Role.USER)


def _app_with_runtime(runtime=None, *, request_context=_admin_ctx) -> FastAPI:
    app = FastAPI()
    app.include_router(console_router)
    app.state.usage_audit_runtime = runtime
    app.dependency_overrides[get_request_context] = request_context

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(_, exc: OpenVikingError):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500),
            content=Response(
                status="error",
                error=ErrorInfo(code=exc.code, message=exc.message, details=exc.details),
            ).model_dump(),
        )

    return app


class FakeConsoleService:
    def __init__(self) -> None:
        self.audit_call = None

    async def token_series(self, **kwargs):
        raise InvalidArgumentError("bad date range")

    async def audit_logs(self, **kwargs):
        self.audit_call = kwargs
        return {"total": 0, "success_rate": 0.0, "page": 2, "page_size": 20, "items": []}


class FakeRuntime:
    def __init__(self, api_service) -> None:
        self.api_service = api_service


@pytest.mark.asyncio
async def test_console_router_returns_disabled_when_usage_audit_not_initialized():
    transport = httpx.ASGITransport(app=_app_with_runtime(None))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/console/dashboard/summary")

    assert response.status_code == 200
    assert response.json()["result"]["enabled"] is False


@pytest.mark.asyncio
async def test_console_router_splits_audit_filters():
    service = FakeConsoleService()
    transport = httpx.ASGITransport(app=_app_with_runtime(FakeRuntime(service)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/console/audit",
            params={
                "page": "2",
                "page_size": "20",
                "status": ["success,error", "5xx"],
                "api_type": "search.find,sessions",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert service.audit_call["ctx"].account_id == "acct-1"
    assert service.audit_call["statuses"] == ["success", "error", "5xx"]
    assert service.audit_call["api_types"] == ["search.find", "sessions"]
    assert service.audit_call["page"] == 2
    assert service.audit_call["page_size"] == 20


@pytest.mark.asyncio
async def test_console_router_rejects_regular_user():
    service = FakeConsoleService()
    transport = httpx.ASGITransport(
        app=_app_with_runtime(FakeRuntime(service), request_context=_user_ctx)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/api/v1/console/audit")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert service.audit_call is None


@pytest.mark.asyncio
async def test_console_router_invalid_arguments_return_http_400():
    service = FakeConsoleService()
    transport = httpx.ASGITransport(app=_app_with_runtime(FakeRuntime(service)))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/api/v1/console/tokens",
            params={"start_date": "2026-05-12", "end_date": "2026-05-01"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
