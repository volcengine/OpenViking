# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""FastAPI app for the standalone OpenViking console service."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .config import (
    ConsoleConfig,
    as_runtime_capabilities,
    load_console_config,
)

PROXY_PREFIX = "/console/api/v1"

_ALLOWED_FORWARD_HEADERS = {
    "x-api-key",
    "authorization",
    "x-openviking-account",
    "x-openviking-user",
    "x-openviking-agent",
    "content-type",
}

_ALLOWED_FORWARD_RESPONSE_HEADERS = {
    # Content negotiation / caching / downloads
    "content-type",
    "content-disposition",
    "cache-control",
    "etag",
    "last-modified",
    # Observability
    "x-request-id",
}


def _error_response(status_code: int, code: str, message: str, details: Optional[dict] = None):
    return JSONResponse(
        status_code=status_code,
        content={
            "status": "error",
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        },
    )


def _copy_forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _ALLOWED_FORWARD_HEADERS:
            headers[key] = value
    return headers


def _copy_forward_response_headers(upstream_response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in upstream_response.headers.items():
        if key.lower() in _ALLOWED_FORWARD_RESPONSE_HEADERS:
            headers[key] = value
    return headers


async def _forward_request(request: Request, upstream_path: str) -> Response:
    """Forward the incoming request to OpenViking upstream."""
    client: httpx.AsyncClient = request.app.state.upstream_client
    body = await request.body()
    try:
        upstream_response = await client.request(
            method=request.method,
            url=upstream_path,
            params=request.query_params,
            content=body,
            headers=_copy_forward_headers(request),
        )
    except httpx.RequestError as exc:
        return _error_response(
            status_code=502,
            code="UPSTREAM_UNAVAILABLE",
            message=f"Failed to reach OpenViking upstream: {exc}",
        )

    content_type = upstream_response.headers.get("content-type", "application/json")
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        media_type=content_type,
        headers=_copy_forward_response_headers(upstream_response),
    )


def _ensure_write_enabled(request: Request) -> Optional[JSONResponse]:
    config: ConsoleConfig = request.app.state.console_config
    if config.write_enabled:
        return None
    return _error_response(
        status_code=403,
        code="WRITE_DISABLED",
        message=(
            "Console write mode is disabled. Start service with --write-enabled "
            "and restart the service to allow write operations."
        ),
    )


def _create_proxy_router() -> APIRouter:
    router = APIRouter(prefix=PROXY_PREFIX, tags=["console"])

    @router.get("/runtime/capabilities")
    async def runtime_capabilities(request: Request):
        config: ConsoleConfig = request.app.state.console_config
        return {"status": "ok", "result": as_runtime_capabilities(config)}

    # ---- Read routes ----

    @router.get("/ov/fs/ls")
    async def fs_ls(request: Request):
        return await _forward_request(request, "/api/v1/fs/ls")

    @router.get("/ov/fs/tree")
    async def fs_tree(request: Request):
        return await _forward_request(request, "/api/v1/fs/tree")

    @router.get("/ov/fs/stat")
    async def fs_stat(request: Request):
        return await _forward_request(request, "/api/v1/fs/stat")

    @router.post("/ov/search/find")
    async def search_find(request: Request):
        return await _forward_request(request, "/api/v1/search/find")

    @router.get("/ov/content/read")
    async def content_read(request: Request):
        return await _forward_request(request, "/api/v1/content/read")

    @router.get("/ov/admin/accounts")
    async def admin_accounts(request: Request):
        return await _forward_request(request, "/api/v1/admin/accounts")

    @router.get("/ov/admin/accounts/{account_id}/users")
    async def admin_users(request: Request, account_id: str):
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}/users")

    @router.get("/ov/system/status")
    async def system_status(request: Request):
        return await _forward_request(request, "/api/v1/system/status")

    @router.get("/ov/observer/{component}")
    async def observer_component(request: Request, component: str):
        return await _forward_request(request, f"/api/v1/observer/{component}")

    # ---- Write routes ----

    @router.post("/ov/fs/mkdir")
    async def fs_mkdir(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/fs/mkdir")

    @router.post("/ov/resources")
    async def add_resource(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/resources")

    @router.post("/ov/resources/temp_upload")
    async def add_resource_temp_upload(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/resources/temp_upload")

    @router.post("/ov/fs/mv")
    async def fs_mv(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/fs/mv")

    @router.delete("/ov/fs")
    async def fs_rm(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/fs")

    @router.post("/ov/admin/accounts")
    async def create_account(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/admin/accounts")

    @router.delete("/ov/admin/accounts/{account_id}")
    async def delete_account(request: Request, account_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}")

    @router.post("/ov/admin/accounts/{account_id}/users")
    async def create_user(request: Request, account_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}/users")

    @router.delete("/ov/admin/accounts/{account_id}/users/{user_id}")
    async def delete_user(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(
            request, f"/api/v1/admin/accounts/{account_id}/users/{user_id}"
        )

    @router.put("/ov/admin/accounts/{account_id}/users/{user_id}/role")
    async def set_user_role(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(
            request,
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/role",
        )

    @router.post("/ov/admin/accounts/{account_id}/users/{user_id}/key")
    async def regenerate_key(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(
            request,
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/key",
        )

    @router.post("/ov/sessions")
    async def create_session(request: Request):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, "/api/v1/sessions")

    @router.post("/ov/sessions/{session_id}/messages")
    async def add_session_message(request: Request, session_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, f"/api/v1/sessions/{session_id}/messages")

    @router.post("/ov/sessions/{session_id}/commit")
    async def commit_session(request: Request, session_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        return await _forward_request(request, f"/api/v1/sessions/{session_id}/commit")

    return router


def create_console_app(
    config: Optional[ConsoleConfig] = None,
    upstream_transport: Optional[httpx.AsyncBaseTransport] = None,
) -> FastAPI:
    """Create console app instance."""
    if config is None:
        config = load_console_config()

    static_dir = Path(__file__).resolve().parent / "static"
    index_file = static_dir / "index.html"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            client: httpx.AsyncClient = app.state.upstream_client
            if not client.is_closed:
                await client.aclose()

    app = FastAPI(
        title="OpenViking Console",
        description="Standalone console for OpenViking HTTP APIs",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.console_config = config
    app.state.upstream_client = httpx.AsyncClient(
        base_url=config.normalized_base_url(),
        timeout=config.request_timeout_sec,
        transport=upstream_transport,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        # Avoid invalid/unsafe combination: allow_credentials + wildcard origin.
        allow_credentials=("*" not in config.cors_origins),
    )

    app.include_router(_create_proxy_router())

    @app.get("/health", include_in_schema=False)
    async def healthz():
        return {"status": "ok", "service": "openviking-console"}

    @app.get("/", include_in_schema=False)
    async def index_root():
        return FileResponse(index_file)

    @app.get("/console", include_in_schema=False)
    async def index_console():
        return FileResponse(index_file)

    @app.get("/console/{path:path}", include_in_schema=False)
    async def console_assets(path: str):
        if path.startswith("api/"):
            return _error_response(status_code=404, code="NOT_FOUND", message="Not found")

        # Prevent directory traversal (e.g. /console/%2e%2e/...)
        static_root = static_dir.resolve()
        try:
            requested_file = (static_dir / path).resolve()
        except OSError:
            return _error_response(status_code=404, code="NOT_FOUND", message="Not found")

        if not requested_file.is_relative_to(static_root):
            return _error_response(status_code=404, code="NOT_FOUND", message="Not found")

        if requested_file.exists() and requested_file.is_file():
            return FileResponse(requested_file)
        return FileResponse(index_file)

    return app
