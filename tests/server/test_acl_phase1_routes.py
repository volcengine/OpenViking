# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Router-level ACL tests for the phase-1 data API permission checks."""

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from openviking.server.auth import get_request_context
from openviking.server.error_mapping import map_exception
from openviking.server.identity import (
    NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
    READ_ONLY_PERMISSION_PROFILE_ID,
    EffectivePermissions,
    RequestContext,
    Role,
)
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS, ErrorInfo, Response
from openviking.server.routers.filesystem import router as filesystem_router
from openviking.server.routers.resources import router as resources_router
from openviking.server.routers.search import router as search_router
from openviking_cli.exceptions import OpenVikingError
from openviking_cli.session.user_id import UserIdentifier


def _build_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(filesystem_router)
    app.include_router(search_router)
    app.include_router(resources_router)

    @app.exception_handler(OpenVikingError)
    async def openviking_error_handler(_: Request, exc: OpenVikingError):
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(exc.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=exc.code,
                    message=exc.message,
                    details=exc.details,
                ),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def general_error_handler(_: Request, exc: Exception):
        mapped = map_exception(exc)
        if mapped is None:
            raise exc
        http_status = ERROR_CODE_TO_HTTP_STATUS.get(mapped.code, 500)
        return JSONResponse(
            status_code=http_status,
            content=Response(
                status="error",
                error=ErrorInfo(
                    code=mapped.code,
                    message=mapped.message,
                    details=mapped.details,
                ),
            ).model_dump(),
        )

    return app


@pytest.fixture
async def acl_client():
    app = _build_test_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, app


def _ctx(
    *,
    profile_id: str,
    permissions: EffectivePermissions,
) -> RequestContext:
    return RequestContext(
        user=UserIdentifier("acme", "alice", "default"),
        role=Role.USER,
        permission_profile=profile_id,
        effective_permissions=permissions,
    )


@pytest.mark.asyncio
async def test_filesystem_ls_denies_without_data_read(acl_client):
    client, app = acl_client
    app.dependency_overrides[get_request_context] = lambda: _ctx(
        profile_id=NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
        permissions=EffectivePermissions.no_access(),
    )
    try:
        resp = await client.get("/api/v1/fs/ls", params={"uri": "viking://"})
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {
        "resource": "viking://",
        "operation": "filesystem.ls",
        "required_permission": "data.read",
        "permission_profile": NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
        "role": "user",
        "effective_permissions": {"data_read": False, "data_write": False},
    }


@pytest.mark.asyncio
async def test_filesystem_mkdir_denies_without_data_write(acl_client):
    client, app = acl_client
    app.dependency_overrides[get_request_context] = lambda: _ctx(
        profile_id=READ_ONLY_PERMISSION_PROFILE_ID,
        permissions=EffectivePermissions.read_only(),
    )
    try:
        resp = await client.post(
            "/api/v1/fs/mkdir",
            json={"uri": "viking://resources/restricted"},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {
        "resource": "viking://resources/restricted",
        "operation": "filesystem.mkdir",
        "required_permission": "data.write",
        "permission_profile": READ_ONLY_PERMISSION_PROFILE_ID,
        "role": "user",
        "effective_permissions": {"data_read": True, "data_write": False},
    }


@pytest.mark.asyncio
async def test_search_find_denies_without_data_read(acl_client):
    client, app = acl_client
    app.dependency_overrides[get_request_context] = lambda: _ctx(
        profile_id=NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
        permissions=EffectivePermissions.no_access(),
    )
    try:
        resp = await client.post(
            "/api/v1/search/find",
            json={"query": "sample", "target_uri": "viking://resources", "limit": 5},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {
        "resource": "viking://resources",
        "operation": "search.find",
        "required_permission": "data.read",
        "permission_profile": NO_DATA_ACCESS_PERMISSION_PROFILE_ID,
        "role": "user",
        "effective_permissions": {"data_read": False, "data_write": False},
    }


@pytest.mark.asyncio
async def test_resources_add_resource_denies_without_data_write(acl_client):
    client, app = acl_client
    app.dependency_overrides[get_request_context] = lambda: _ctx(
        profile_id=READ_ONLY_PERMISSION_PROFILE_ID,
        permissions=EffectivePermissions.read_only(),
    )
    try:
        resp = await client.post(
            "/api/v1/resources",
            json={"path": "https://example.com/demo.md", "reason": "fixture"},
        )
    finally:
        app.dependency_overrides.pop(get_request_context, None)

    assert resp.status_code == 403
    assert resp.json()["error"]["details"] == {
        "operation": "resources.add_resource",
        "required_permission": "data.write",
        "permission_profile": READ_ONLY_PERMISSION_PROFILE_ID,
        "role": "user",
        "effective_permissions": {"data_read": True, "data_write": False},
    }
