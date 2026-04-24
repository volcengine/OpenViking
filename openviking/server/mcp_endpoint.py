# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MCP (Model Context Protocol) endpoint for OpenViking server.

Exposes 5 tools to Claude Code (or any MCP client) via streamable HTTP:
  search, read, store, forget, health

Mounted on the FastAPI app at /mcp. The MCP session manager lifecycle is
tied to the FastAPI app lifespan (not a sub-app lifespan) so the task group
is always initialized before requests arrive.

Identity headers (X-OpenViking-Account, X-OpenViking-User, X-OpenViking-Agent)
are extracted from HTTP request scope and propagated via contextvars.
"""

from __future__ import annotations

import contextvars
import hashlib
from contextlib import asynccontextmanager
from typing import Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openviking.server.auth import resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext, Role
from openviking_cli.exceptions import (
    InvalidArgumentError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Identity propagation via contextvars
# ---------------------------------------------------------------------------

_mcp_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "_mcp_ctx", default=None
)


def _get_ctx() -> RequestContext:
    ctx = _mcp_ctx.get()
    if ctx is None:
        return RequestContext(
            user=UserIdentifier("default", "default", "default"),
            role=Role.ROOT,
        )
    return ctx


class _IdentityASGIMiddleware:
    """ASGI middleware: delegates to auth.resolve_identity (the same function
    used by all REST API routes) so authentication logic is never duplicated."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        request = Request(scope)
        try:
            identity = await resolve_identity(
                request,
                x_api_key=request.headers.get("x-api-key"),
                authorization=request.headers.get("authorization"),
                x_openviking_account=request.headers.get("x-openviking-account"),
                x_openviking_user=request.headers.get("x-openviking-user"),
                x_openviking_agent=request.headers.get("x-openviking-agent"),
            )
        except (UnauthenticatedError, PermissionDeniedError, InvalidArgumentError) as exc:
            status = 401 if isinstance(exc, UnauthenticatedError) else (
                403 if isinstance(exc, PermissionDeniedError) else 400
            )
            resp = JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32001, "message": str(exc)}},
                status_code=status,
            )
            return await resp(scope, receive, send)

        ctx = RequestContext(
            user=UserIdentifier(
                identity.account_id or "default",
                identity.user_id or "default",
                identity.agent_id or "default",
            ),
            role=identity.role,
            namespace_policy=identity.namespace_policy,
        )
        token = _mcp_ctx.set(ctx)
        try:
            return await self.app(scope, receive, send)
        finally:
            _mcp_ctx.reset(token)


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "openviking",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

SEARCH_TARGETS: dict[str, list[str]] = {
    "memories": ["viking://user/memories", "viking://agent/memories"],
    "resources": ["viking://resources"],
    "skills": ["viking://agent/skills"],
}


def _is_memory_uri(uri: str) -> bool:
    return ("viking://user/" in uri or "viking://agent/" in uri) and "/memories/" in uri


def _md5_short(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()[:12]


# -- search ----------------------------------------------------------------

@mcp.tool()
async def search(query: str, scope: str = "all", limit: int = 6) -> str:
    """Search OpenViking context database. Auto-recall already injects top matches — use this for deeper or narrower searches. Prefer search over manual directory traversal."""
    service = get_service()
    ctx = _get_ctx()
    scopes = [scope] if scope != "all" else ["memories", "resources", "skills"]
    score_threshold = 0.35

    results: list[dict] = []
    for s in scopes:
        for uri in SEARCH_TARGETS.get(s, []):
            try:
                r = await service.search.find(
                    query=query, ctx=ctx, target_uri=uri,
                    limit=limit, score_threshold=None,
                )
                items = getattr(r, "to_dict", lambda: r)()
                bucket = items.get(s, []) if isinstance(items, dict) else []
                for item in bucket:
                    if (item.get("score", 0) or 0) >= score_threshold:
                        results.append({**item, "_type": s.rstrip("s")})
            except Exception:
                pass

    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    seen: set[str] = set()
    picked: list[dict] = []
    for item in results:
        key = item.get("uri", "")
        if key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) >= limit:
            break

    if not picked:
        return "No matching context found."

    lines = []
    for item in picked:
        score = item.get("score", 0)
        abstract = (item.get("abstract") or item.get("overview") or "(no abstract)").strip()
        typ = item.get("_type", "memory")
        lines.append(f"- [{typ} {score * 100:.0f}%] {item['uri']}\n    {abstract}")

    return f"Found {len(picked)} item(s):\n\n" + "\n".join(lines) + "\n\nUse the read tool to expand a URI."


# -- read ------------------------------------------------------------------

async def _read_one(uri: str) -> str:
    service = get_service()
    ctx = _get_ctx()
    try:
        result = await service.fs.read(uri, ctx=ctx)
        if isinstance(result, str) and result.strip():
            return result
    except Exception:
        pass
    try:
        entries = await service.fs.ls(uri, ctx=ctx)
        if entries:
            lines = []
            for e in entries:
                name = e.get("name", "?") if isinstance(e, dict) else getattr(e, "name", "?")
                is_dir = e.get("isDir", False) if isinstance(e, dict) else getattr(e, "is_dir", False)
                lines.append(f"[{'dir' if is_dir else 'file'}] {name}")
            return "\n".join(lines)
    except Exception:
        pass
    return f"(nothing found at {uri})"


@mcp.tool()
async def read(uris: str | list[str]) -> str:
    """Read one or more viking:// URIs. Pass a single URI or a list for batch reads. Directory URIs return a listing. Prefer search to find relevant URIs rather than navigating directories."""
    uri_list = uris if isinstance(uris, list) else [uris]
    if len(uri_list) == 1:
        return await _read_one(uri_list[0])
    parts = []
    for uri in uri_list:
        text = await _read_one(uri)
        parts.append(f"=== {uri} ===\n{text}")
    return "\n\n".join(parts)


# -- store -----------------------------------------------------------------

_COMMIT_THRESHOLD = 4000

@mcp.tool()
async def store(text: str, role: str = "user") -> str:
    """Store information into OpenViking long-term memory. Use when the user says 'remember this', shares preferences, important facts, or decisions worth persisting."""
    service = get_service()
    ctx = _get_ctx()
    session_id = "cc-mcpstore-" + _md5_short(
        f"{ctx.user.account_id}|{ctx.user.user_id}|{ctx.user.agent_id}"
    )
    session = await service.sessions.get(session_id, ctx, auto_create=True)
    from openviking.session.parts import TextPart
    session.add_message(role, [TextPart(text=text)])
    pending = getattr(session, "pending_tokens", 0)
    committed = False
    if pending >= _COMMIT_THRESHOLD:
        await service.sessions.commit_async(session_id, ctx)
        committed = True
    if committed:
        return f"Stored. {pending} tokens committed for extraction."
    return f"Stored. {pending} pending tokens (commits at {_COMMIT_THRESHOLD})."


# -- forget ----------------------------------------------------------------

@mcp.tool()
async def forget(uri: str = "", query: str = "") -> str:
    """Delete a memory from OpenViking. Provide an exact URI for direct deletion, or a search query to find and delete matching memories."""
    service = get_service()
    ctx = _get_ctx()
    if uri:
        if not _is_memory_uri(uri):
            return f"Refusing to delete non-memory URI: {uri}"
        await service.fs.delete(uri, ctx=ctx)
        return f"Deleted: {uri}"
    if not query:
        return "Provide either uri or query."
    candidates = []
    for target in SEARCH_TARGETS["memories"]:
        try:
            r = await service.search.find(query=query, ctx=ctx, target_uri=target, limit=20, score_threshold=None)
            items = getattr(r, "to_dict", lambda: r)()
            for item in items.get("memories", []):
                if item.get("level") == 2 and _is_memory_uri(item.get("uri", "")):
                    candidates.append(item)
        except Exception:
            pass
    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    if not candidates:
        return "No matching memories found."
    top = candidates[0]
    if len(candidates) == 1 and (top.get("score", 0) or 0) >= 0.85:
        await service.fs.delete(top["uri"], ctx=ctx)
        return f"Deleted: {top['uri']}"
    lines = []
    for item in candidates[:10]:
        score = item.get("score", 0) or 0
        abstract = (item.get("abstract") or "?").strip()
        lines.append(f"- {item['uri']} — {abstract} ({score * 100:.0f}%)")
    return f"Found {len(candidates)} candidates. Specify the exact URI:\n\n" + "\n".join(lines)


# -- health ----------------------------------------------------------------

@mcp.tool()
async def health() -> str:
    """Check whether the OpenViking server is healthy."""
    try:
        service = get_service()
        return f"OpenViking is healthy (service initialized, storage: {type(service.viking_fs).__name__})"
    except Exception as e:
        return f"OpenViking is unhealthy: {e}"


# ---------------------------------------------------------------------------
# App factory + lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def mcp_lifespan():
    """Run the MCP session manager. Call this inside the FastAPI lifespan."""
    async with mcp.session_manager.run():
        logger.info("MCP endpoint ready (5 tools: search, read, store, forget, health)")
        yield


def create_mcp_app() -> ASGIApp:
    """Create the MCP ASGI app with identity middleware.

    IMPORTANT: call `mcp_lifespan()` inside the FastAPI lifespan BEFORE
    serving requests. The session manager task group must be initialized.
    """
    # streamable_http_app() lazily creates the session_manager.
    # We call it to trigger creation, then extract the route handler.
    starlette_app = mcp.streamable_http_app()
    handler = starlette_app.routes[0].app  # StreamableHTTPASGIApp
    return _IdentityASGIMiddleware(handler)
