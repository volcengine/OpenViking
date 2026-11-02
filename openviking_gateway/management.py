# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Gateway management: OV identity and independent object-write authorization."""

import asyncio
import copy
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote

import aiohttp
import orjson
from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from .capture import reset_capture
from .client import VikingError
from .config import MOUNT_PATH
from .models import (
    CaptureReset,
    KeyRequest,
    Policy,
    Upstream,
    UpstreamTestRequest,
    validation_detail,
)
from .overview import overview_summary
from .protocols import text_content
from .proxy import default_policy, fill_key, upstream_headers, upstream_key, upstream_url
from .records import RecordKind as K
from .storage import delete_account_data, delete_user_data, digest
from .tool_catalog import select_tools
from .tool_protocols import ResponseCapture, tool_protocol

PREFIX = "/admin"
MANAGEMENT_HEADER = "x-openviking-gateway-management-key"
# Fields a VLM re-import refreshes; everything else may have been edited in the gateway.
VLM_SOURCE_FIELDS = (
    "base_url",
    "api_key",
    "headers",
    "protocol",
    "vendor",
    "coding_plan",
    "append_endpoint_path",
    "key_header",
)
UPSTREAM_TEST_PATHS = {
    "anthropic": "/v1/messages",
    "chat": "/v1/chat/completions",
    "responses": "/v1/responses",
}
UPSTREAM_TEST_PROMPT = "Reply with only OK."
UPSTREAM_TEST_RESPONSE_LIMIT = 16_384
DETAILS = {
    "management_key_invalid": "The gateway management key is incorrect.",
    "management_key_required": "Changing upstreams and profiles requires the management key.",
    "other_account": "This OpenViking API key cannot access the selected account.",
    "forbidden": "This OpenViking role cannot perform the operation.",
    "not_found": "The object does not exist.",
    "in_use": "Gateway keys still use this object; revoke them first.",
    "invalid_configuration": "The request body is invalid.",
    "unknown_user": "The user does not exist in this account.",
    "user_required": "OpenViking root is not an account user; choose the user to issue for.",
    "user_key_unreadable": "OpenViking stores only a hash of this user's API key; paste the key.",
    "unknown_policy": "The context profile does not exist.",
    "unknown_upstream": "An upstream does not exist.",
    "vlm_import_unavailable": "This gateway cannot read OpenViking model settings.",
    "vlm_import_forbidden": "Importing OpenViking model settings requires the management key "
    "or, without one, OpenViking root.",
}


def upstream_test_payload(upstream, model, tools):
    """Build one small, non-streaming generation request for the configured protocol."""
    model = upstream.get("aliases", {}).get(model, model)
    common = {"model": model, "stream": False}
    if upstream["protocol"] == "responses":
        payload = {
            **common,
            "input": UPSTREAM_TEST_PROMPT,
            "max_output_tokens": 64,
            "store": False,
        }
    else:
        payload = {
            **common,
            "messages": [{"role": "user", "content": UPSTREAM_TEST_PROMPT}],
            "max_tokens": 64,
        }
        if upstream["protocol"] == "chat" and upstream.get("vendor") == "openai":
            payload["max_completion_tokens"] = payload.pop("max_tokens")
    if tools:
        tool_protocol(upstream["protocol"]).add_tools(payload, tools)
    return payload


def upstream_test_output(protocol, body):
    """Return whether a JSON body contains generated text for the protocol."""
    if not isinstance(body, dict) or body.get("error"):
        return False
    try:
        capture = ResponseCapture(protocol)
        capture.nonstream(body)
        return bool(capture.message and text_content(capture.message).strip())
    except (AttributeError, KeyError, TypeError):
        return False


def fail(status, reason, detail=None, **extra):
    raise HTTPException(status, {"reason": reason, "detail": detail or DETAILS[reason], **extra})


async def read_body(request, model, prepare=None):
    """Validate a JSON body; error details never echo submitted values."""
    try:
        payload = await request.json()
        return model.model_validate(prepare(payload) if prepare else payload)
    except ValidationError as error:
        fail(422, "invalid_configuration", validation_detail(error))
    except ValueError:
        fail(422, "invalid_configuration")


def request_credential(request):
    return request.headers.get("x-api-key") or request.headers.get(
        "authorization", ""
    ).removeprefix("Bearer ")


@dataclass
class Caller:
    account_id: str
    credential: str
    identity: dict | None
    manager: bool
    can_write: bool

    @property
    def role(self):
        return self.identity.get("role") if self.identity else None

    @property
    def user_id(self):
        return self.identity.get("user_id") if self.identity else None

    @property
    def log_user(self):
        return self.user_id if self.role == "user" else None


async def caller_tool_catalog(app, management, who):
    """Read the account's MCP tools with a credential OpenViking accepts."""
    key = who.credential
    if who.role == "root" and who.identity.get("auth_mode") != "dev":
        # OV refuses root on /mcp. Every caller sees the same catalog, so read it with
        # a key issued in this account, or fall back to the last catalog read.
        keys = await management.list(who.account_id, "keys")
        key = next((item["openviking_key"] for item in keys), "")
    return await app.state.viking.tools(key) if key else app.state.viking.last_tools or []


async def caller(request, *, require_identity=True, write=False):
    state = request.app.state
    configured = state.config.management_key
    supplied = request.headers.get(MANAGEMENT_HEADER)
    manager = bool(
        configured and supplied and hmac.compare_digest(supplied.encode(), configured.encode())
    )
    if supplied is not None and not manager:
        fail(401, "management_key_invalid")
    credential = request_credential(request)
    identity = None
    if credential or require_identity or not manager:
        try:
            identity = await state.viking.identify(credential)
        except VikingError as error:
            if error.reason == "openviking_unavailable":
                raise VikingError("openviking_unreachable", 503) from error
            raise
    account = request.query_params.get("account_id") or (identity or {}).get(
        "account_id", "default"
    )
    # A management key may select any account for objects only. It cannot elevate OV operations.
    if identity and account != identity["account_id"] and identity["role"] != "root":
        if require_identity or not manager:
            fail(403, "other_account")
    can_write = manager or not configured
    if write and not can_write:
        fail(403, "management_key_required")
    return Caller(account, credential, identity, manager, can_write)


async def register_user(app, account_id, user_id, ttl=None):
    """Record a new key's user and lift an earlier deletion of their gateway data."""
    for protocol in ("anthropic", "chat", "responses"):
        await app.state.store.allow_scope(digest(account_id + "\0" + user_id + "\0" + protocol))
    await app.state.management.save(account_id, "users", user_id, {"user_id": user_id}, ttl=ttl)


async def openviking_credential(app, key):
    """Confirm and cache an OpenViking key with its deployment's tenant partition."""
    identifier = digest("openviking\0" + key)
    partitioned = app.state.config.tenancy == "key"
    if credential := await app.state.management.credential(identifier):
        # A tenancy change moves the key to another partition: confirm it again.
        if (credential["account"] == "k_" + identifier) == partitioned:
            return credential
        await app.state.management.delete(credential["account"], "keys", identifier)
    identity = await app.state.viking.health(key)
    account, user_id = identity["account_id"], identity["user_id"]
    if partitioned:
        account = "k_" + identifier
    ttl = app.state.config.session_ttl_seconds
    stored = {
        "source": "openviking",
        "openviking_account": identity["account_id"],
        "openviking_key": key,
        "user_id": user_id,
        # Current keys are "<account>.<user>.<secret>": only the secret tells them apart.
        "prefix": key.rsplit(".", 1)[-1][:8],
        "created_at": time.time(),
    }
    await app.state.management.save(account, "keys", identifier, stored, ttl=ttl)
    await register_user(app, account, user_id, ttl=ttl)
    return await app.state.management.credential(identifier)


async def import_vlm_objects(app, account_id):
    """Save the host's VLM candidates as upstreams; a re-import keeps gateway-side edits."""
    management = app.state.management
    result = await app.state.vlm_source(account_id)
    imported, confirmation, skipped = [], [], list(result.get("skipped", []))
    for candidate in result.get("upstreams", []):
        payload = dict(candidate)
        identifier = payload.pop("id")
        if previous := await management.get(account_id, "upstreams", identifier):
            kept = {k: v for k, v in previous.items() if k not in {"id", "revision"}}
            payload = {**kept, **{k: payload[k] for k in VLM_SOURCE_FIELDS if k in payload}}
        try:
            value = Upstream.model_validate(payload).model_dump()
        except ValueError:
            # Validation errors can include secrets from the input; never expose them.
            skipped.append({"id": identifier, "reason": "invalid_configuration"})
            continue
        await management.save(account_id, "upstreams", identifier, value)
        row = {"id": identifier, "name": value["name"]}
        (
            confirmation if value["coding_plan"] and not value["allow_coding_plan"] else imported
        ).append(row)
    return {"imported": imported, "needs_confirmation": confirmation, "skipped": skipped}


def can_import(app, who):
    return bool(
        app.state.vlm_source
        and (who.manager if app.state.config.management_key else who.role == "root")
    )


def register_management(app):
    router = APIRouter(prefix=PREFIX, tags=["gateway"])
    management, store = app.state.management, app.state.store

    @router.get("/access")
    async def access(request: Request):
        who = await caller(request, require_identity=False)
        return {
            "managed": bool(app.state.config.management_key),
            "can_write_objects": who.can_write,
            "can_import_vlm": can_import(app, who),
            "can_issue_for_others": who.role in {"root", "admin"}
            and (who.identity or {}).get("auth_mode") != "dev",
            "role": who.role,
            "account_id": who.account_id,
            "user_id": who.user_id,
            "warnings": app.state.warnings,
        }

    @router.get("/overview")
    async def overview(request: Request):
        who = await caller(request)
        logs = await management.logs(who.account_id, 10000, user_id=who.log_user)
        return {
            **overview_summary(logs, time.time()),
            "openviking": app.state.health,
            "log_retention_days": app.state.config.log_retention_days,
        }

    @router.get("/logs")
    async def logs(request: Request, limit: int = 200):
        who = await caller(request)
        return await management.logs(who.account_id, max(1, min(limit, 1000)), user_id=who.log_user)

    @router.get("/guides")
    async def guides(request: Request):
        await caller(request)
        return {
            "base_url": (
                app.state.config.public_url or f"{request.url.scheme}://{request.url.netloc}"
            )
            + MOUNT_PATH,
            "public_url_configured": bool(app.state.config.public_url),
        }

    @router.get("/tools")
    async def tools(request: Request):
        who = await caller(request)
        catalog = await caller_tool_catalog(app, management, who)
        return [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                **({"annotations": t["annotations"]} if "annotations" in t else {}),
            }
            for t in catalog
        ]

    @router.delete("/users/{user_id}/data")
    async def delete_user(user_id: str, request: Request):
        who = await caller(request)
        if who.role not in {"root", "admin"}:
            fail(403, "forbidden")
        await delete_user_data(management, store, who.account_id, user_id)
        return {"deleted": True}

    @router.delete("/account/data")
    async def delete_account(request: Request):
        who = await caller(request)
        if who.role != "root":
            fail(403, "forbidden")
        await delete_account_data(management, store, who.account_id)
        return {"deleted": True}

    @router.post("/upstreams/import-vlm")
    async def import_vlm(request: Request):
        who = await caller(request, require_identity=False)
        if app.state.vlm_source is None:
            fail(404, "vlm_import_unavailable")
        if not can_import(app, who):
            fail(403, "vlm_import_forbidden")
        return await import_vlm_objects(app, who.account_id)

    def object_routes(kind, model):
        @router.get(f"/{kind}")
        async def list_objects(request: Request):
            who = await caller(request, require_identity=False)
            values = await management.list(who.account_id, kind)
            return [public_object(kind, value, read_only=not who.can_write) for value in values]

        @router.get(f"/{kind}/{{identifier}}")
        async def get_object(identifier: str, request: Request):
            who = await caller(request, require_identity=False)
            value = await management.get(who.account_id, kind, identifier)
            if not value:
                fail(404, "not_found")
            return public_object(kind, value, read_only=not who.can_write)

        @router.put(f"/{kind}/{{identifier}}")
        async def save_object(identifier: str, request: Request):
            who = await caller(request, require_identity=False, write=True)
            if kind == "upstreams":
                previous = await management.get(who.account_id, kind, identifier) or {}
                value = await read_body(
                    request, model, lambda payload: keep_upstream_secrets(payload, previous)
                )
            else:
                value = await read_body(request, model)
            saved = await management.save(who.account_id, kind, identifier, value.model_dump())
            return public_object(kind, saved)

        @router.delete(f"/{kind}/{{identifier}}")
        async def delete_object(identifier: str, request: Request):
            who = await caller(request, require_identity=False, write=True)
            count = sum(
                key.get("policy_id") == identifier
                if kind == "policies"
                else identifier in key.get("upstream_ids", [])
                for key in await management.list(who.account_id, "keys")
            )
            if count:
                fail(409, "in_use", key_count=count)
            await management.delete(who.account_id, kind, identifier)
            return {"deleted": True}

    object_routes("upstreams", Upstream)
    object_routes("policies", Policy)

    def hidden(who, key):
        """A user sees only the keys issued for that user."""
        return who.role == "user" and key["user_id"] != who.user_id

    @router.get("/keys")
    async def list_keys(request: Request):
        who = await caller(request)
        values = await management.list(who.account_id, "keys", user_id=who.log_user)
        return [public_object("keys", value) for value in values]

    @router.get("/keys/{identifier}")
    async def get_key(identifier: str, request: Request):
        who = await caller(request)
        value = await management.get(who.account_id, "keys", identifier)
        if not value or hidden(who, value):
            fail(404, "not_found")
        return public_object("keys", value)

    # Keys are immutable: no PUT route, so a replacement must be issued.
    @router.delete("/keys/{identifier}")
    async def revoke_key(identifier: str, request: Request):
        who = await caller(request)
        value = await management.get(who.account_id, "keys", identifier)
        if value and hidden(who, value):
            fail(404, "not_found")
        await management.delete(who.account_id, "keys", identifier)
        return {"deleted": True}

    @router.post("/keys")
    async def issue_key(request: Request):
        who = await caller(request)
        value = await read_body(request, KeyRequest)
        root = who.role == "root" and who.identity.get("auth_mode") != "dev"
        if root and not value.user_id and not value.openviking_key:
            fail(400, "user_required")
        user_id = value.user_id or who.user_id
        if who.role == "user" and user_id != who.user_id:
            fail(403, "forbidden")
        key = value.openviking_key
        if not key:
            if user_id == who.user_id and who.identity["account_id"] == who.account_id and not root:
                key = who.credential
            else:
                # name matches by pattern; the exact user_id comparison below picks the row.
                rows = await app.state.viking.request(
                    "GET",
                    f"/api/v1/admin/accounts/{quote(who.account_id, safe='')}/users"
                    f"?include_credentials=true&name={quote(user_id, safe='')}",
                    who.credential,
                )
                if isinstance(rows, dict):
                    rows = rows.get("users", [])
                selected = next(
                    (
                        r
                        for r in rows
                        if r["user_id"] == user_id and r.get("role") in {"admin", "user"}
                    ),
                    None,
                )
                if not selected:
                    fail(400, "unknown_user")
                key = selected.get("api_key")
                if not key:
                    fail(409, "user_key_unreadable")
        identity = await app.state.viking.health(key)
        if identity["account_id"] != who.account_id:
            fail(403, "other_account")
        if (value.user_id or who.role == "user") and identity["user_id"] != user_id:
            fail(403, "forbidden")
        if not await management.get(who.account_id, "policies", value.policy_id):
            fail(400, "unknown_policy")
        for identifier in value.upstream_ids:
            if not await management.get(who.account_id, "upstreams", identifier):
                fail(400, "unknown_upstream")
        secret = "ovgw_" + secrets.token_urlsafe(32)
        stored = {
            **value.model_dump(),
            "openviking_key": key,
            "user_id": identity["user_id"],
            "prefix": secret[:12],
            "created_at": time.time(),
        }
        result = await management.save(who.account_id, "keys", digest(secret), stored)
        await register_user(app, who.account_id, identity["user_id"])
        return {**public_object("keys", result), "key": secret}

    @router.post("/keys/{identifier}/capture/reset")
    async def reset(identifier: str, data: CaptureReset, request: Request):
        who = await caller(request)
        key = await management.get(who.account_id, "keys", identifier)
        if not key or hidden(who, key):
            fail(404, "not_found")
        scope = digest(who.account_id + "\0" + key["user_id"] + "\0" + data.protocol)
        for session in (digest(data.session), data.session):
            if (K.ROOT, "") in await store.replay.read(scope, session, [""]):
                break
        else:
            fail(404, "not_found")
        await reset_capture(store.capture, scope, session, "manual_reset")
        return {"status": "ready", "message": "Capture will resync from the next request history"}

    @router.post("/upstreams/{identifier}/test")
    async def test_upstream(identifier: str, request: Request):
        who = await caller(request, require_identity=False, write=True)
        upstream = await management.get(who.account_id, "upstreams", identifier)
        if not upstream:
            fail(404, "not_found")
        data = await read_body(request, UpstreamTestRequest)
        assert data is not None
        generating = data.model is not None
        kind = "model" if generating else "connection"
        if not generating and not upstream.get("append_endpoint_path", True):
            # An exact endpoint has no model list; only a generation test can reach it.
            url = upstream_url(upstream, UPSTREAM_TEST_PATHS[upstream["protocol"]])
            return {"ok": False, "reason": "exact_endpoint_untestable", "url": url, "kind": kind}
        path = UPSTREAM_TEST_PATHS[upstream["protocol"]] if generating else "/v1/models"
        url = upstream_url(upstream, path)
        if generating and upstream.get("coding_plan") and not upstream.get("allow_coding_plan"):
            return {"ok": False, "reason": "coding_plan_blocked", "url": url, "kind": kind}
        try:
            headers = upstream_headers(upstream, {})
            target = fill_key(url, upstream_key(upstream, {}))
        except HTTPException as error:
            if error.status_code != 401:
                raise
            # No stored key (passthrough keys come from clients): nothing is sent.
            return {"ok": False, "reason": "upstream_key_missing", "url": url, "kind": kind}
        payload = None
        if generating:
            headers.setdefault("content-type", "application/json")
            if upstream["protocol"] == "anthropic":
                headers.setdefault("anthropic-version", "2023-06-01")
            tools = []
            if upstream.get("allow_gateway_tools", True):
                policy = await default_policy(management, app.state.config, who.account_id)
                try:
                    tools = select_tools(
                        await caller_tool_catalog(app, management, who),
                        policy,
                    )
                except VikingError:
                    pass
            payload = upstream_test_payload(upstream, data.model, tools)
        try:
            async with app.state.http.request(
                "POST" if generating else "GET",
                target,
                data=orjson.dumps(payload) if payload is not None else None,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=min(app.state.config.upstream_timeout_seconds, 60)
                ),
                allow_redirects=False,
            ) as response:
                status = response.status
                raw = bytearray()
                while len(raw) <= UPSTREAM_TEST_RESPONSE_LIMIT:
                    chunk = await response.content.read(UPSTREAM_TEST_RESPONSE_LIMIT + 1 - len(raw))
                    if not chunk:
                        break
                    raw.extend(chunk)
                truncated = len(raw) > UPSTREAM_TEST_RESPONSE_LIMIT
                response_text = raw[:UPSTREAM_TEST_RESPONSE_LIMIT].decode("utf-8", errors="replace")
                result = {
                    "ok": False,
                    "status": status,
                    "url": url,
                    "kind": kind,
                }
                if not generating and status in {404, 405}:
                    result.update(ok=True, reason="models_unavailable")
                elif not generating and status == 429:
                    result.update(ok=True, reason="rate_limited")
                elif not generating and 200 <= status < 300:
                    try:
                        body = json.loads(response_text) if not truncated else None
                    except (TypeError, ValueError):
                        body = None
                    valid = (
                        isinstance(body, dict)
                        and isinstance(body.get("data"), list)
                        and not body.get("error")
                    )
                    result.update(
                        ok=valid,
                        reason="passed" if valid else "upstream_key_rejected",
                    )
                elif 200 <= status < 300:
                    try:
                        body = json.loads(response_text) if not truncated else None
                    except (TypeError, ValueError):
                        body = None
                    valid = upstream_test_output(upstream["protocol"], body)
                    result.update(
                        ok=valid,
                        reason="passed" if valid else "invalid_response",
                    )
                elif status in {401, 403}:
                    result["reason"] = "upstream_key_rejected"
                elif status == 429:
                    result["reason"] = "rate_limited"
                elif status in {400, 404, 405, 422}:
                    result["reason"] = "model_request_rejected"
                else:
                    result["reason"] = "upstream_unavailable"
                if not result["ok"]:
                    response_headers = {}
                    for name in response.headers:
                        if name in response_headers:
                            continue
                        values = response.headers.getall(name)
                        response_headers[name] = values[0] if len(values) == 1 else values
                    result["response"] = {
                        "http_version": (f"HTTP/{response.version.major}.{response.version.minor}"),
                        "status": status,
                        "reason": response.reason,
                        "headers": response_headers,
                        "body": response_text,
                        **({"truncated": True} if truncated else {}),
                    }
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return {"ok": False, "reason": "upstream_unavailable", "url": url, "kind": kind}

    app.include_router(router)


def public_object(kind, value, *, read_only=False):
    value = copy.deepcopy(value)
    if kind == "upstreams":
        value["has_api_key"] = bool(value.pop("api_key", ""))
        headers = value.pop("headers", {})
        if not read_only:
            value["header_names"] = list(headers)
    if kind == "keys":
        value.pop("openviking_key", None)
        if value.get("source") == "openviking":
            # Listed in the shape of an issued key, but it stores no binding: resolve_binding
            # gives it the account's defaults per request.
            value.update(name="", policy_id="", upstream_ids=[], models=[])
    return value


def keep_upstream_secrets(payload, previous):
    """Preserve omitted secrets only while the destination remains unchanged.

    A blank ``api_key`` keeps the stored key. ``headers`` omitted keeps every stored header;
    otherwise the submitted names are the new header set, and a blank value keeps the stored
    value for that name (or drops the name when nothing is stored).
    """
    if not isinstance(payload, dict):
        raise ValueError("expected an upstream object")
    same_base = str(payload.get("base_url") or "").rstrip("/") == previous.get(
        "base_url", ""
    ).rstrip("/")
    if not payload.get("api_key"):
        payload["api_key"] = previous.get("api_key", "") if same_base else ""
    stored = previous.get("headers", {}) if same_base else {}
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
