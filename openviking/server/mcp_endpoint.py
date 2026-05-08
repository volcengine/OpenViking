# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MCP (Model Context Protocol) endpoint for OpenViking server.

Exposes tools to Claude Code (or any MCP client) via streamable HTTP:
  find, search, read, list, remember, add_resource, grep, glob, forget, health

Mounted on the FastAPI app at /mcp. The MCP session manager lifecycle is
tied to the FastAPI app lifespan (not a sub-app lifespan) so the task group
is always initialized before requests arrive.

Identity headers (X-OpenViking-Account, X-OpenViking-User, X-OpenViking-Agent)
are extracted from HTTP request scope and propagated via contextvars.
"""

from __future__ import annotations

import contextvars
from contextlib import asynccontextmanager
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openviking.server.auth import resolve_identity
from openviking.server.dependencies import get_service
from openviking.server.identity import RequestContext
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
        raise UnauthenticatedError("MCP request identity not set")
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
            status = (
                401
                if isinstance(exc, UnauthenticatedError)
                else (403 if isinstance(exc, PermissionDeniedError) else 400)
            )
            resp = JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": str(exc)}},
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
# MCP server tools (aligned with vikingbot/agent/tools/ov_file.py)
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "openviking",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# -- find / search ---------------------------------------------------------


@mcp.tool()
async def find(query: str, target_uri: str = "", limit: int = 10, min_score: float = 0.35) -> str:
    """Fast semantic retrieval without session context. Returns ranked memories, resources, and skills with URI, abstract, and score."""
    service = get_service()
    result = await service.search.find(
        query=query,
        ctx=_get_ctx(),
        target_uri=target_uri,
        limit=limit,
        score_threshold=min_score,
    )
    return _format_search_result(result)


@mcp.tool()
async def search(
    query: str,
    target_uri: str = "",
    session_id: Optional[str] = None,
    limit: int = 10,
    min_score: float = 0.35,
) -> str:
    """Deep semantic retrieval with optional session context and intent analysis. Returns ranked memories, resources, and skills with URI, abstract, and score."""
    service = get_service()
    ctx = _get_ctx()
    session = None
    if session_id:
        session = service.sessions.session(ctx, session_id)
        await session.load()
    result = await service.search.search(
        query=query,
        ctx=ctx,
        target_uri=target_uri,
        session=session,
        limit=limit,
        score_threshold=min_score,
    )
    return _format_search_result(result)


def _format_search_result(result) -> str:
    items = []
    for ctx_type, contexts in [
        ("memory", result.memories),
        ("resource", result.resources),
        ("skill", result.skills),
    ]:
        for m in contexts:
            items.append((ctx_type, m))

    if not items:
        return "No matching context found."

    lines = []
    for ctx_type, m in items:
        abstract = (
            getattr(m, "abstract", "") or getattr(m, "overview", "") or "(no abstract)"
        ).strip()
        score = getattr(m, "score", 0.0)
        lines.append(f"- [{ctx_type} {score * 100:.0f}%] {m.uri}\n    {abstract}")

    return (
        f"Found {len(items)} item(s):\n\n"
        + "\n".join(lines)
        + "\n\nUse the read tool to expand a URI."
    )


# -- read ------------------------------------------------------------------


@mcp.tool()
async def read(uris: str | list[str]) -> str:
    """Read full content from one or more viking:// file URIs. Pass a single URI string or a list for batch reads. For directory listing, use the list tool instead."""
    import asyncio

    service = get_service()
    ctx = _get_ctx()
    uri_list = uris if isinstance(uris, list) else [uris]
    semaphore = asyncio.Semaphore(10)

    async def _read_one(uri: str) -> str:
        async with semaphore:
            try:
                body = await service.fs.read(uri, ctx=ctx)
                if isinstance(body, str) and body.strip():
                    return body
            except Exception:
                pass
            return f"(nothing found at {uri})"

    if len(uri_list) == 1:
        return await _read_one(uri_list[0])

    results = await asyncio.gather(*[_read_one(u) for u in uri_list])
    parts = []
    for uri, text in zip(uri_list, results, strict=True):
        parts.append(f"=== {uri} ===\n{text}")
    return "\n\n".join(parts)


# -- list ------------------------------------------------------------------


@mcp.tool(name="list")
async def ls(uri: str, recursive: bool = False) -> str:
    """List files and subdirectories under a viking:// directory URI. Use recursive=true for deep listing."""
    service = get_service()
    ctx = _get_ctx()

    entries = await service.fs.ls(uri, ctx=ctx, recursive=recursive, output="original")
    if not entries:
        return f"(no entries under {uri})"

    lines = []
    for e in entries:
        name = e.get("name", "?") if isinstance(e, dict) else getattr(e, "name", "?")
        is_dir = e.get("isDir", False) if isinstance(e, dict) else getattr(e, "is_dir", False)
        entry_uri = e.get("uri", "") if isinstance(e, dict) else getattr(e, "uri", "")
        if recursive and entry_uri:
            lines.append(f"[{'dir' if is_dir else 'file'}] {entry_uri}")
        else:
            lines.append(f"[{'dir' if is_dir else 'file'}] {name}")
    return "\n".join(lines)


# -- remember --------------------------------------------------------------


class StoreMessage(BaseModel):
    role: Literal["user", "assistant"] = Field(description="Message role")
    content: str = Field(description="Message text content")


@mcp.tool()
async def remember(messages: list[StoreMessage]) -> str:
    """Store information into OpenViking long-term memory. Use when the user says 'remember this', shares preferences, important facts, or decisions worth persisting."""
    import uuid

    from openviking.message.part import TextPart

    service = get_service()
    ctx = _get_ctx()
    session_id = f"mcp-store-{uuid.uuid4().hex[:12]}"
    session = await service.sessions.get(session_id, ctx, auto_create=True)
    for msg in messages:
        if msg.content:
            session.add_message(
                msg.role,
                [TextPart(text=msg.content)],
                role_id=ctx.resolve_role_id(msg.role),
            )
    await service.sessions.commit_async(session_id, ctx)
    return f"Stored {len(messages)} message(s) and committed for memory extraction."


# -- add_resource ----------------------------------------------------------


_LOCAL_FILE_HINT = (
    "MCP add_resource only accepts remote URLs (http(s)://, git@, ssh://, git://). "
    "For local files or directories, use the `ov` CLI:\n"
    "  1. Try first: ov add-resource <path>\n"
    "     (if `ov` is already on PATH, this is all you need)\n"
    "  2. If `ov` is not installed, run:\n"
    "     curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/crates/ov_cli/install.sh | bash\n"
    "  3. Only if connecting to a remote / multi-tenant OpenViking server, "
    "configure ~/.openviking/ovcli.conf:\n"
    '       {"url": "https://your-host", "api_key": "your-key"}'
)


@mcp.tool()
async def add_resource(path: str, description: str = "") -> str:
    """Add a remote resource (HTTP/HTTPS URL or git URL) to OpenViking. Asynchronous — processed in the background. Local file paths are not supported here; use the `ov add-resource` CLI for local files."""
    from openviking.server.local_input_guard import require_remote_resource_source

    service = get_service()
    ctx = _get_ctx()
    try:
        path = require_remote_resource_source(path)
    except PermissionDeniedError:
        return f"Error: {_LOCAL_FILE_HINT}"
    try:
        result = await service.resources.add_resource(
            path=path,
            ctx=ctx,
            reason=description,
            wait=False,
            enforce_public_remote_targets=True,
        )
        root_uri = result.get("root_uri", "")
        return (
            f"Resource added: {root_uri}"
            if root_uri
            else "Resource added (processing in background)."
        )
    except Exception as e:
        return f"Error adding resource: {e}"


# -- grep ------------------------------------------------------------------


@mcp.tool()
async def grep(
    uri: str, pattern: str | list[str], case_insensitive: bool = False, node_limit: int = 10
) -> str:
    """Search content in viking:// files using regex patterns (like grep). Supports multiple patterns searched concurrently. Use this for exact text matching; use the search tool for semantic retrieval."""
    import asyncio

    service = get_service()
    ctx = _get_ctx()
    patterns = [pattern] if isinstance(pattern, str) else pattern
    semaphore = asyncio.Semaphore(10)

    async def _grep_one(p: str) -> tuple[str, list[dict]]:
        async with semaphore:
            try:
                result = await service.fs.grep(
                    uri,
                    p,
                    ctx=ctx,
                    case_insensitive=case_insensitive,
                    node_limit=node_limit,
                )
                return (p, result.get("matches", []))
            except Exception:
                return (p, [])

    results = await asyncio.gather(*[_grep_one(p) for p in patterns])

    merged: dict[str, list[tuple]] = {}
    total = 0
    for p, matches in results:
        total += len(matches)
        for m in matches:
            m_uri = m.get("uri", "?")
            merged.setdefault(m_uri, []).append((m.get("line", "?"), m.get("content", ""), p))

    if not merged:
        return f"No matches found for pattern(s): {', '.join(patterns)}"

    lines = [f"Found {total} match(es) across {len(patterns)} pattern(s):"]
    for m_uri, hits in merged.items():
        hits.sort(key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0)
        lines.append(f"\n{m_uri}")
        for line_no, content, p in hits:
            lines.append(f"  L{line_no} [{p}]: {content}")
    return "\n".join(lines)


# -- glob ------------------------------------------------------------------


@mcp.tool()
async def glob(pattern: str, uri: str = "viking://", node_limit: int = 100) -> str:
    """Find viking:// files matching a glob pattern (e.g. **/*.md, *.py). Use this for filename matching; use the search tool for content-based retrieval."""
    service = get_service()
    ctx = _get_ctx()

    try:
        result = await service.fs.glob(pattern, ctx=ctx, uri=uri, node_limit=node_limit)
    except Exception as e:
        return f"Error: {e}"

    matches = result.get("matches", [])
    if not matches:
        return f"No files found matching: {pattern}"

    lines = [f"Found {len(matches)} file(s):"]
    for m in matches:
        m_uri = m.get("uri", str(m)) if isinstance(m, dict) else str(m)
        lines.append(f"  {m_uri}")
    return "\n".join(lines)


# -- forget ----------------------------------------------------------------


@mcp.tool()
async def forget(uri: str) -> str:
    """Permanently delete a viking:// URI from OpenViking. This is irreversible. Only use when the user explicitly asks to forget or delete something. Always confirm with the user before calling this tool. Use the search tool first to find the exact URI, then pass it here."""
    service = get_service()
    ctx = _get_ctx()
    await service.fs.rm(uri, ctx=ctx)
    return f"Deleted: {uri}"


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
        logger.info(
            "MCP endpoint ready (10 tools: find, search, read, list, remember, add_resource, grep, glob, forget, health)"
        )
        yield


def create_mcp_app() -> ASGIApp:
    """Create the MCP ASGI app with identity middleware.

    IMPORTANT: call `mcp_lifespan()` inside the FastAPI lifespan BEFORE
    serving requests. The session manager task group must be initialized.
    """
    starlette_app = mcp.streamable_http_app()
    handler = starlette_app.routes[0].app
    return _IdentityASGIMiddleware(handler)
