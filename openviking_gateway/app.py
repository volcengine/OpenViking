# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Standalone ASGI host: authentication, routing, management and byte streaming."""

import asyncio
import contextlib
import copy
import hmac
import logging
import secrets
import time
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from .capture import CaptureWorker, reset_capture
from .client import VikingClient, VikingError
from .config import OpenVikingGatewayConfig
from .kernel import MemoryKernel
from .models import CaptureReset, KeyRequest, Policy, Upstream
from .proxy import ProxyRequest, filtered_headers, upstream_headers, upstream_url
from .records import RecordKind as K
from .storage import ManagementStore, SQLiteKernelStore, digest

logger = logging.getLogger(__name__)


def public_object(kind, value):
    value = copy.deepcopy(value)
    if kind == "upstreams":
        value["has_api_key"] = bool(value.pop("api_key", ""))
        value["header_names"] = list(value.pop("headers", {}))
    if kind == "keys":
        value.pop("openviking_key", None)
    return value


def keep_upstream_secrets(payload, previous):
    """Fill write-only upstream secrets the admin left blank from the stored upstream.

    A blank ``api_key`` keeps the stored key. ``headers`` omitted keeps every stored header;
    otherwise the submitted names are the new header set, and a blank value keeps the stored
    value for that name (or drops the name when nothing is stored).
    """
    if not isinstance(payload, dict):
        raise ValueError("expected an upstream object")
    if not payload.get("api_key"):
        payload["api_key"] = previous.get("api_key", "")
    stored = previous.get("headers", {})
    headers = payload.get("headers")
    if headers is None:
        payload["headers"] = stored
    elif isinstance(headers, dict):
        payload["headers"] = {
            name: value or stored[name]
            for name, value in headers.items()
            if value or name in stored
        }
    return payload


def overview_summary(logs):
    """Aggregate newest-first request-log records into the management overview."""
    requests = [log for log in logs if log.get("kind") != "capture"]
    groups = {}
    for label, kinds in (("first_call", {"user"}), ("continuation", {"continuation"})):
        subset = [log for log in requests if log.get("kind") in kinds]
        total = sum(
            log.get("first_upstream_input_tokens", log.get("input_tokens", 0)) for log in subset
        )
        cached = sum(
            log.get("first_upstream_cached_tokens", log.get("cached_tokens", 0)) for log in subset
        )
        hidden_calls = 0
        if label == "continuation":
            total += sum(log.get("hidden_upstream_input_tokens", 0) for log in requests)
            cached += sum(log.get("hidden_upstream_cached_tokens", 0) for log in requests)
            hidden_calls = sum(log.get("hidden_upstream_calls", 0) for log in requests)
        groups[label] = {
            "requests": len(subset) + hidden_calls,
            "input_tokens": total,
            "cached_tokens": cached,
            "cache_hit_ratio": cached / total if total else 0,
        }
    degradations = {}
    for log in logs:
        if reason := log.get("degradation"):
            degradations[reason] = degradations.get(reason, 0) + 1
    # "disabled" means recall was skipped (off, budget spent or query too short).
    recalls = [log for log in requests if log.get("recall_reason", "disabled") != "disabled"]
    capture = {}
    for log in logs:
        if log.get("session") and log.get("capture_status"):
            capture.setdefault((log["session"], log.get("protocol")), log["capture_status"])
    return {
        "requests": len(requests),
        "last_request_at": requests[0].get("time") if requests else None,
        "output_tokens": sum(log.get("output_tokens", 0) for log in requests),
        "cache": groups,
        "degradations": degradations,
        "recall_count": sum(log.get("recall_count", 0) for log in requests),
        "recall_requests": len(recalls),
        "recall_ms": sum(log.get("recall_ms", 0) for log in recalls) / len(recalls)
        if recalls
        else 0,
        "capture_issues": {
            status: sum(1 for value in capture.values() if value == status)
            for status in ("retrying", "paused")
        },
        "sample_limit": 10000,
    }


def create_app(config: OpenVikingGatewayConfig | None = None):
    if config is None:
        from .cli import load_config

        config = load_config()
    encryption_key, admin_token = config.secrets()
    store = SQLiteKernelStore(config.directory / "kernel.sqlite3", encryption_key)
    management = ManagementStore(config.directory / "management.sqlite3", encryption_key)

    @asynccontextmanager
    async def lifespan(app):
        await store.initialize()
        await management.initialize()
        async with aiohttp.ClientSession(
            auto_decompress=False,
            trust_env=False,
            connector=aiohttp.TCPConnector(limit=1024, limit_per_host=512),
        ) as http:
            app.state.http = http
            app.state.viking = VikingClient(http, config.openviking_url, config.min_server_version)
            app.state.kernel = MemoryKernel(store, app.state.viking)
            app.state.worker = CaptureWorker(store, management, app.state.viking)
            app.state.health = {"status": "starting"}
            await refresh_health(app)
            if app.state.health.get("auth_mode") == "dev" and config.host not in {
                "127.0.0.1",
                "::1",
                "localhost",
            }:
                raise ValueError(
                    "OpenViking Gateway must bind to loopback when OpenViking uses dev authentication"
                )
            task = asyncio.create_task(maintenance(app))
            try:
                yield
            finally:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                store.close()
                management.close()

    app = FastAPI(
        title="OpenViking Gateway",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.store, app.state.management, app.state.config = store, management, config

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
        while True:
            try:
                if time.monotonic() >= health_at:
                    await refresh_health(app)
                    health_at = time.monotonic() + config.health_interval_seconds
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

    @app.exception_handler(VikingError)
    async def viking_error(_request, error):
        return JSONResponse({"error": {"message": error.reason}}, status_code=error.status)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "openviking-gateway", "openviking": app.state.health}

    async def authenticate(request):
        if app.state.health.get("auth_mode") == "dev" and config.host not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise HTTPException(503, "Dev authentication requires a loopback gateway")
        key = request.headers.get("x-api-key") or request.headers.get(
            "authorization", ""
        ).removeprefix("Bearer ")
        if key.startswith("sk-ant-oat"):
            raise HTTPException(403, "Claude subscription OAuth credentials are not supported")
        credential = await management.authenticate(key)
        if not credential:
            raise HTTPException(401, "Invalid or revoked OpenViking Gateway key")
        return credential

    def admin_account(request):
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied, admin_token):
            raise HTTPException(401, "Invalid gateway management credential")
        account = request.headers.get("x-openviking-account", "")
        if not account:
            raise HTTPException(400, "Management requires a verified account")
        return account

    @app.get("/admin/overview")
    async def overview(request: Request):
        logs = await management.logs(admin_account(request), 10000)
        return {
            **overview_summary(logs),
            "openviking": app.state.health,
            "log_retention_days": config.log_retention_days,
        }

    @app.get("/admin/logs")
    async def logs(request: Request, limit: int = 200):
        return await management.logs(admin_account(request), max(1, min(limit, 1000)))

    @app.post("/admin/keys/{identifier}/capture/reset")
    async def reset_session_capture(identifier: str, data: CaptureReset, request: Request):
        account = admin_account(request)
        key = await management.get(account, "keys", identifier)
        if not key:
            raise HTTPException(404, "Gateway key not found")
        scope = digest(account + "\0" + key["user_id"] + "\0" + data.protocol)
        # A session header value, or the hashed id request logs show.
        for session in (digest(data.session), data.session):
            if (K.ROOT, "") in await store.replay.read(scope, session, [""]):
                break
        else:
            raise HTTPException(404, "Gateway session not found")
        await reset_capture(store.capture, scope, session, "manual_reset")
        return {"status": "ready", "message": "Capture will resync from the next request history"}

    @app.get("/admin/guides")
    async def guides(request: Request):
        admin_account(request)
        return {
            "base_url": config.public_url or config.url,
            "public_url_configured": bool(config.public_url),
        }

    # Registered before /admin/{kind}/{identifier}, which would otherwise shadow account/data.
    @app.delete("/admin/users/{user_id}/data")
    async def delete_user_data(user_id: str, request: Request):
        account = admin_account(request)
        for key in await management.list(account, "keys"):
            if key["user_id"] == user_id:
                await management.delete(account, "keys", key["id"])
        for protocol in ("anthropic", "chat", "responses"):
            await store.expire(0, digest(account + "\0" + user_id + "\0" + protocol))
        await management.delete(account, "users", user_id)
        return {"deleted": True}

    @app.delete("/admin/account/data")
    async def delete_account_data(request: Request):
        account = admin_account(request)
        users = {user["user_id"] for user in await management.list(account, "users")}
        for user in users:
            await delete_user_data(user, request)
        await management.delete_account(account)
        return {"deleted": True}

    @app.get("/admin/tools")
    async def list_tools(request: Request):
        # Every user sees the same MCP tools, so any key OpenViking accepts will do.
        catalog, failure = [], None
        for key in await management.list(admin_account(request), "keys"):
            try:
                catalog, failure = await app.state.viking.tools(key["openviking_key"]), None
                break
            except VikingError as error:
                failure = error
                if error.status not in {401, 403}:
                    break
        if failure:
            # Studio would take a passed-through 401 for its own sign-in failing.
            raise VikingError(failure.reason, 502)
        return [
            {
                "name": tool["name"],
                "description": tool.get("description", ""),
                **({"annotations": tool["annotations"]} if "annotations" in tool else {}),
            }
            for tool in catalog
        ]

    @app.get("/admin/{kind}")
    async def list_objects(kind: str, request: Request):
        if kind not in {"upstreams", "policies", "keys"}:
            raise HTTPException(404)
        return [public_object(kind, v) for v in await management.list(admin_account(request), kind)]

    @app.api_route("/admin/{kind}/{identifier}", methods=["PUT", "DELETE"])
    async def change_object(kind: str, identifier: str, request: Request):
        account = admin_account(request)
        if kind not in {"upstreams", "policies", "keys"}:
            raise HTTPException(404)
        if request.method == "DELETE":
            if kind != "keys":
                keys = await management.list(account, "keys")
                if any(
                    k["policy_id"] == identifier
                    if kind == "policies"
                    else identifier in k["upstream_ids"]
                    for k in keys
                ):
                    raise HTTPException(
                        409, "Revoke or update dependent keys before deleting this object"
                    )
            await management.delete(account, kind, identifier)
            return {"deleted": True}
        if kind == "keys":
            raise HTTPException(405, "Issue a replacement key using POST /admin/keys")
        try:
            payload = await request.json()
            previous = await management.get(account, kind, identifier)
            if kind == "upstreams":
                payload = keep_upstream_secrets(payload, previous or {})
                value = Upstream.model_validate(payload).model_dump()
            else:
                value = Policy.model_validate(payload).model_dump()
        except (ValidationError, ValueError):
            # Validation messages can contain the submitted secret. Do not echo input.
            raise HTTPException(
                422, "Invalid gateway configuration; check field names, types and limits"
            )
        return public_object(kind, await management.save(account, kind, identifier, value))

    @app.post("/admin/keys")
    async def issue_key(request: Request):
        account = admin_account(request)
        try:
            value = KeyRequest.model_validate(await request.json())
        except (ValidationError, ValueError):
            raise HTTPException(422, "Invalid key configuration")
        identity = await app.state.viking.health(value.openviking_key)
        if identity["account_id"] != account:
            raise HTTPException(403, "OpenViking key belongs to another account")
        if not await management.get(account, "policies", value.policy_id):
            raise HTTPException(400, "Unknown context policy")
        for identifier in value.upstream_ids:
            if not await management.get(account, "upstreams", identifier):
                raise HTTPException(400, "Unknown upstream")
        secret = "ovgw_" + secrets.token_urlsafe(32)
        stored = {
            **value.model_dump(),
            "user_id": identity["user_id"],
            "prefix": secret[:12],
            "created_at": time.time(),
        }
        result = await management.save(account, "keys", digest(secret), stored)
        for protocol in ("anthropic", "chat", "responses"):
            await store.allow_scope(digest(account + "\0" + identity["user_id"] + "\0" + protocol))
        await management.save(
            account, "users", identity["user_id"], {"user_id": identity["user_id"]}
        )
        return {**public_object("keys", result), "key": secret}

    @app.post("/admin/upstreams/{identifier}/test")
    async def test_upstream(identifier: str, request: Request):
        upstream = await management.get(admin_account(request), "upstreams", identifier)
        if not upstream:
            raise HTTPException(404)
        try:
            async with app.state.http.get(
                upstream_url(upstream, "/v1/models"),
                headers=upstream_headers(upstream, {}),
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                return {"ok": response.status < 400, "status": response.status}
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {"ok": False, "reason": "upstream_unavailable"}

    @app.get("/api/v3/models")
    @app.get("/v1/models")
    async def models(request: Request):
        credential = await authenticate(request)
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

    @app.post("/gateway/uploads")
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
                config.openviking_url + "/api/v1/resources/temp_upload",
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

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy(path: str, request: Request):
        credential = await authenticate(request)
        return await ProxyRequest(app, request, path, credential).run()

    return app
