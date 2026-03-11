# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""FastAPI app for the standalone OpenViking console service."""

from __future__ import annotations

import json
import re
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
_CONSOLE_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_SAFE_PATH_SEGMENT = re.compile(r"^[\w.@+-]+$")

_ALLOWED_FORWARD_HEADERS = {
    "accept",
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


def _is_json_content_type(content_type: str) -> bool:
    value = (content_type or "").lower()
    return "application/json" in value or "+json" in value


def _should_default_telemetry(upstream_path: str) -> bool:
    if upstream_path in {"/api/v1/search/find", "/api/v1/resources"}:
        return True
    return upstream_path.startswith("/api/v1/sessions/") and upstream_path.endswith("/commit")


def _with_default_telemetry(request: Request, upstream_path: str, body: bytes) -> bytes:
    if request.method.upper() != "POST":
        return body
    if not _should_default_telemetry(upstream_path):
        return body
    if not _is_json_content_type(request.headers.get("content-type", "")):
        return body

    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(payload, dict):
        return body

    payload.setdefault("telemetry", True)
    return json.dumps(payload).encode("utf-8")


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
    body = _with_default_telemetry(request, upstream_path, body)
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


def _validate_path_param(value: str, name: str) -> Optional[JSONResponse]:
    if not value or value in {".", ".."} or not _SAFE_PATH_SEGMENT.match(value):
        return _error_response(
            status_code=400,
            code="INVALID_PARAMETER",
            message=f"Invalid {name}",
        )
    return None


def _validate_fs_path(path_str: str) -> Optional[JSONResponse]:
    """Validate file system path to prevent directory traversal attacks."""
    if not path_str:
        # Empty path is allowed (means current directory)
        return None

    # Reject absolute paths
    if path_str.startswith("/") or path_str.startswith("\\"):
        return _error_response(
            status_code=400,
            code="INVALID_PATH",
            message="Absolute paths are not allowed",
        )

    # Check for Windows drive letters (C:, D:, etc.)
    if len(path_str) >= 2 and path_str[1] == ":":
        return _error_response(
            status_code=400,
            code="INVALID_PATH",
            message="Absolute paths are not allowed",
        )

    # Check for parent directory traversal
    if ".." in path_str:
        return _error_response(
            status_code=400,
            code="INVALID_PATH",
            message="Path traversal sequences (..) are not allowed",
        )

    return None


def _create_proxy_router() -> APIRouter:
    router = APIRouter(prefix=PROXY_PREFIX, tags=["console"])

    @router.get("/runtime/capabilities")
    async def runtime_capabilities(request: Request):
        config: ConsoleConfig = request.app.state.console_config
        return {"status": "ok", "result": as_runtime_capabilities(config)}

    # ---- Read routes ----

    @router.get("/ov/fs/ls")
    async def fs_ls(request: Request):
        path = request.query_params.get("path", "")
        invalid = _validate_fs_path(path)
        if invalid:
            return invalid
        return await _forward_request(request, "/api/v1/fs/ls")

    @router.get("/ov/fs/tree")
    async def fs_tree(request: Request):
        path = request.query_params.get("path", "")
        invalid = _validate_fs_path(path)
        if invalid:
            return invalid
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
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}/users")

    @router.get("/ov/system/status")
    async def system_status(request: Request):
        return await _forward_request(request, "/api/v1/system/status")

    @router.get("/ov/observer/{component}")
    async def observer_component(request: Request, component: str):
        invalid = _validate_path_param(component, "component")
        if invalid:
            return invalid
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
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}")

    @router.post("/ov/admin/accounts/{account_id}/users")
    async def create_user(request: Request, account_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        return await _forward_request(request, f"/api/v1/admin/accounts/{account_id}/users")

    @router.delete("/ov/admin/accounts/{account_id}/users/{user_id}")
    async def delete_user(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        invalid = _validate_path_param(user_id, "user_id")
        if invalid:
            return invalid
        return await _forward_request(
            request, f"/api/v1/admin/accounts/{account_id}/users/{user_id}"
        )

    @router.put("/ov/admin/accounts/{account_id}/users/{user_id}/role")
    async def set_user_role(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        invalid = _validate_path_param(user_id, "user_id")
        if invalid:
            return invalid
        return await _forward_request(
            request,
            f"/api/v1/admin/accounts/{account_id}/users/{user_id}/role",
        )

    @router.post("/ov/admin/accounts/{account_id}/users/{user_id}/key")
    async def regenerate_key(request: Request, account_id: str, user_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        invalid = _validate_path_param(account_id, "account_id")
        if invalid:
            return invalid
        invalid = _validate_path_param(user_id, "user_id")
        if invalid:
            return invalid
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
        invalid = _validate_path_param(session_id, "session_id")
        if invalid:
            return invalid
        return await _forward_request(request, f"/api/v1/sessions/{session_id}/messages")

    @router.post("/ov/sessions/{session_id}/commit")
    async def commit_session(request: Request, session_id: str):
        blocked = _ensure_write_enabled(request)
        if blocked:
            return blocked
        invalid = _validate_path_param(session_id, "session_id")
        if invalid:
            return invalid
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

    def _console_file_response(path: Path) -> FileResponse:
        return FileResponse(path, headers=_CONSOLE_NO_STORE_HEADERS)

    @app.get("/health", include_in_schema=False)
    async def healthz():
        return {"status": "ok", "service": "openviking-console"}

    @app.get("/", include_in_schema=False)
    async def index_root():
        return _console_file_response(index_file)

    @app.get("/console", include_in_schema=False)
    async def index_console():
        return _console_file_response(index_file)

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
            return _console_file_response(requested_file)
        return _console_file_response(index_file)

    return app
