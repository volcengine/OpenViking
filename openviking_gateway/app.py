# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gateway ASGI app and the small API its hosts use.

Hosts mount ``create_app`` at ``MOUNT_PATH`` and call ``seed_defaults``,
``import_vlm_objects`` and ``delete_data``; ``standalone`` and the OpenViking server add
their own routes and startup checks.
"""

import asyncio
import contextlib
import logging
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from .capture import CaptureWorker
from .client import VikingClient, VikingError
from .config import PUBLIC_OPENVIKING_URLS, GatewayConfig, normalize_url
from .kernel import MemoryKernel
from .management import import_vlm_objects, register_management, request_credential
from .models import Policy
from .proxy import ProxyRequest, filtered_headers
from .storage import ManagementStore, SQLiteKernelStore, delete_account_data, delete_user_data

logger = logging.getLogger(__name__)
VIKING_DETAILS = {
    "openviking_unavailable": "OpenViking is unreachable.",
    "openviking_unreachable": "OpenViking is unreachable.",
    "openviking_http_401": "OpenViking rejected the API key.",
    "openviking_identity_missing": "OpenViking returned no identity for the API key.",
    "trusted_mode_unsupported": "OpenViking Gateway does not support trusted authentication.",
    "root_key_not_allowed": "A root API key cannot back a gateway key; use a user's API key.",
    "openviking_version_mismatch": "OpenViking Server is older than this gateway requires.",
}


def create_app(config: GatewayConfig, *, openviking_url: str, vlm_source=None, encryptor=None):
    """Build the gateway; ``vlm_source(account_id)`` returns ``{upstreams, skipped}`` dicts.

    ``encryptor`` is an OpenViking ``FileEncryptor``; without one, values are stored as plaintext.
    """
    store = SQLiteKernelStore(config.directory / "kernel.sqlite3", encryptor)
    management = ManagementStore(config.directory / "management.sqlite3", encryptor)

    @asynccontextmanager
    async def lifespan(app):
        try:
            await store.initialize()
            await management.initialize()
            async with aiohttp.ClientSession(
                auto_decompress=False,
                trust_env=False,
                connector=aiohttp.TCPConnector(limit=1024, limit_per_host=512),
            ) as http:
                app.state.http = http
                app.state.viking = VikingClient(http, openviking_url, config.min_server_version)
                app.state.kernel = MemoryKernel(store, app.state.viking)
                app.state.worker = CaptureWorker(store, management, app.state.viking)
                # The first probe runs in maintenance: an embedded server is not listening yet.
                app.state.health = {"status": "starting"}
                task = asyncio.create_task(maintenance(app))
                try:
                    yield
                finally:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        finally:
            # Storage threads may still wait on this loop for the encryptor.
            await asyncio.to_thread(store.close)
            await asyncio.to_thread(management.close)

    app = FastAPI(
        title="OpenViking Gateway",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.management, app.state.config = store, management, config
    app.state.openviking_url = openviking_url.rstrip("/")
    app.state.vlm_source = vlm_source
    app.state.warnings = (
        ["public_openviking_without_management_key"]
        if not config.management_key and normalize_url(openviking_url) in PUBLIC_OPENVIKING_URLS
        else []
    )

    async def refresh_health(app):
        try:
            app.state.health = await app.state.viking.health(require_identity=False)
            app.state.viking.unavailable_reason = ""
        except VikingError as error:
            app.state.health = {"status": "degraded", "reason": error.reason}
            app.state.viking.unavailable_reason = error.reason

    async def maintenance(app):
        health_at = 0
        cleanup_at = 0
        retry = 1
        reached = False
        while True:
            try:
                if time.monotonic() >= health_at:
                    await refresh_health(app)
                    if app.state.health.get("status") == "degraded":
                        health_at = time.monotonic() + retry
                        # An embedded host starts OV after this loop: poll every second until then.
                        if reached:
                            retry = min(retry * 2, config.health_interval_seconds)
                    else:
                        health_at = time.monotonic() + config.health_interval_seconds
                        retry, reached = 1, True
                if time.monotonic() >= cleanup_at:
                    await store.expire(time.time() - config.session_ttl_days * 86400)
                    await management.expire_logs(time.time() - config.log_retention_days * 86400)
                    cleanup_at = time.monotonic() + 3600
                if not await app.state.worker.once():
                    await asyncio.sleep(1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("OpenViking Gateway maintenance failed")
                await asyncio.sleep(1)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, error):
        return JSONResponse(
            {
                "reason": "invalid_configuration",
                "detail": "; ".join(item["msg"] for item in error.errors()),
            },
            status_code=422,
        )

    @app.exception_handler(VikingError)
    async def viking_error(_request, error):
        return JSONResponse(
            {
                "reason": error.reason,
                "detail": VIKING_DETAILS.get(error.reason, "OpenViking request failed."),
                **(
                    {"required_version": config.min_server_version}
                    if error.reason == "openviking_version_mismatch"
                    else {}
                ),
            },
            status_code=error.status,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request, error):
        detail = (
            error.detail
            if isinstance(error.detail, dict)
            else {"reason": "gateway_http_" + str(error.status_code), "detail": error.detail}
        )
        return JSONResponse(detail, status_code=error.status_code, headers=error.headers)

    async def authenticate(request):
        """Return the gateway key and a degradation when OV could not confirm it."""
        key = request_credential(request)
        if key.startswith("sk-ant-oat"):
            raise HTTPException(403, "Claude subscription OAuth credentials are not supported")
        credential = await management.authenticate(key)
        invalid = "Invalid gateway key. It may have been revoked, or removed because its OpenViking API key was regenerated. Issue a new key in OpenViking Studio → Gateway → Keys."
        if not credential:
            raise HTTPException(401, invalid)
        if app.state.viking.unavailable_reason:
            # Like recall, skip the check while OV is down instead of waiting for its timeout.
            return credential, "revocation_check_unavailable"
        try:
            identity = await app.state.viking.identify(credential["openviking_key"])
        except VikingError as error:
            if error.reason != "openviking_http_401":
                # Unconfirmed is not revoked: like recall, the request degrades instead.
                return credential, "revocation_check_unavailable"
            identity = None
        # Dev identities are always default/default, so they say nothing about this key.
        if identity is None or (
            identity.get("auth_mode") != "dev"
            and (
                identity["account_id"] != credential["account"]
                or identity["user_id"] != credential["user_id"]
            )
        ):
            await management.delete(credential["account"], "keys", credential["id"])
            raise HTTPException(401, invalid)
        return credential, None

    register_management(app)

    @app.get("/api/compatible/v1/models")
    @app.get("/api/v3/models")
    @app.get("/v1/models")
    async def models(request: Request):
        credential, _ = await authenticate(request)
        values = await management.list(credential["account"], "upstreams")
        names = sorted(
            {
                model
                for u in values
                if u["id"] in credential["upstream_ids"] and u.get("enabled", True)
                for model in [*u.get("models", []), *u.get("aliases", {})]
                if not credential["models"] or model in credential["models"]
            }
        )
        return {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "owned_by": "openviking-gateway"} for name in names
            ],
        }

    @app.websocket("/api/v3/responses")
    @app.websocket("/v1/responses")
    async def websocket_fallback(websocket: WebSocket):
        await websocket.send_denial_response(
            Response(status_code=426, headers={"Upgrade": "HTTP/1.1"})
        )

    @app.post("/uploads")
    async def proxy_upload(request: Request):
        token = request.query_params.get("token", "")
        if not token or len(token) > 16384:
            raise HTTPException(400, "A signed upload token is required")
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("multipart/form-data;"):
            raise HTTPException(415, "Expected a multipart file upload")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > config.max_body_bytes:
                raise HTTPException(413, "Upload exceeds configured body limit")
        try:
            async with app.state.http.post(
                openviking_url + "/api/v1/resources/temp_upload",
                params={"token": token},
                data=bytes(raw),
                headers={"content-type": content_type, "accept-encoding": "identity"},
                timeout=aiohttp.ClientTimeout(total=120),
                allow_redirects=False,
            ) as response:
                return Response(
                    await response.read(),
                    status_code=response.status,
                    headers=filtered_headers(response.headers),
                )
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise HTTPException(502, "OpenViking upload is unavailable")

    @app.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.api_route("/api/v3/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @app.api_route(
        "/api/compatible/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy(path: str, request: Request):
        credential, degradation = await authenticate(request)
        # The path below the host's mount, with its /v1, /api/v3 or /api/compatible/v1 prefix.
        route_path = request.url.path.removeprefix(request.scope.get("root_path", ""))
        proxied = ProxyRequest(app, request, route_path.lstrip("/"), credential)
        if degradation:
            proxied.metrics["degradation"] = degradation
        return await proxied.run()

    return app


async def seed_defaults(app, account_id):
    """Import VLM upstreams into an empty account and add the fixed "default" profile."""
    management = app.state.management
    if app.state.vlm_source and not await management.list(account_id, "upstreams"):
        await import_vlm_objects(app, account_id)
    if not await management.get(account_id, "policies", "default"):
        await management.save(account_id, "policies", "default", Policy().model_dump())


async def delete_data(app, account_id, user_id=None):
    """Erase gateway state for a deleted OpenViking user, or the whole account."""
    if user_id is None:
        await delete_account_data(app.state.management, app.state.store, account_id)
    else:
        await delete_user_data(app.state.management, app.state.store, account_id, user_id)
