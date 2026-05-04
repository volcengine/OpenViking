# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""MCP (Model Context Protocol) endpoint for OpenViking server.

Exposes 7 tools to Claude Code (or any MCP client) via streamable HTTP:
  search, read, list, store, add_resource, forget, health

Mounted on the FastAPI app at /mcp. The MCP session manager lifecycle is
tied to the FastAPI app lifespan (not a sub-app lifespan) so the task group
is always initialized before requests arrive.

Identity headers (X-OpenViking-Account, X-OpenViking-User, X-OpenViking-Agent)
are extracted from HTTP request scope and propagated via contextvars.
"""

from __future__ import annotations

import contextvars
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import quote

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from openviking.server.auth import resolve_identity
from openviking.server.dependencies import get_server_config, get_service
from openviking.server.identity import RequestContext
from openviking.server.local_input_guard import TEMP_FILE_ID_RE, is_remote_resource_source
from openviking.server.routers.resources import _resolve_temp_or_path
from openviking.server.upload_token_store import upload_token_store
from openviking_cli.exceptions import (
    InvalidArgumentError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger
from openviking_cli.utils.config.open_viking_config import get_openviking_config

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Identity propagation via contextvars
# ---------------------------------------------------------------------------

_mcp_ctx: contextvars.ContextVar[Optional[RequestContext]] = contextvars.ContextVar(
    "_mcp_ctx", default=None
)

# URL hints from the incoming request, captured by middleware so MCP tools can
# reconstruct the agent-facing public URL without knowing about ASGI/Starlette.
# Only used as a fallback when neither OPENVIKING_PUBLIC_BASE_URL nor
# ServerConfig.public_base_url is set.
_request_url_ctx: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "_request_url_ctx", default=None
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
        url_info = {
            "x_forwarded_proto": request.headers.get("x-forwarded-proto"),
            "x_forwarded_host": request.headers.get("x-forwarded-host"),
            "host": request.headers.get("host"),
        }
        ctx_token = _mcp_ctx.set(ctx)
        url_token = _request_url_ctx.set(url_info)
        try:
            return await self.app(scope, receive, send)
        finally:
            _mcp_ctx.reset(ctx_token)
            _request_url_ctx.reset(url_token)


# ---------------------------------------------------------------------------
# MCP server + 7 tools (aligned with vikingbot/agent/tools/ov_file.py)
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "openviking",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# -- search ----------------------------------------------------------------


@mcp.tool()
async def search(query: str, target_uri: str = "", limit: int = 10, min_score: float = 0.35) -> str:
    """Search OpenViking context database (memories, resources, skills). Returns ranked results with URI, abstract, and score. Leave target_uri empty to search everything, or pass a viking:// URI to narrow scope."""
    service = get_service()
    ctx = _get_ctx()

    result = await service.search.find(
        query=query,
        ctx=ctx,
        target_uri=target_uri,
        limit=limit,
        score_threshold=min_score,
    )

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
        abstract = (m.abstract or m.overview or "(no abstract)").strip()
        lines.append(f"- [{ctx_type} {m.score * 100:.0f}%] {m.uri}\n    {abstract}")

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


# -- store -----------------------------------------------------------------


class StoreMessage(BaseModel):
    role: Literal["user", "assistant"] = Field(description="Message role")
    content: str = Field(description="Message text content")


@mcp.tool()
async def store(messages: list[StoreMessage]) -> str:
    """Store information into OpenViking long-term memory. Use when the user says 'remember this', shares preferences, important facts, or decisions worth persisting."""
    import uuid

    from openviking.message.part import TextPart

    service = get_service()
    ctx = _get_ctx()
    session_id = f"mcp-store-{uuid.uuid4().hex[:12]}"
    session = await service.sessions.get(session_id, ctx, auto_create=True)
    for msg in messages:
        if msg.content:
            session.add_message(msg.role, [TextPart(text=msg.content)])
    await service.sessions.commit_async(session_id, ctx)
    return f"Stored {len(messages)} message(s) and committed for memory extraction."


# -- add_resource ----------------------------------------------------------


_DEFAULT_UPLOAD_TTL_SECONDS = 600


def _resolve_public_base_url() -> tuple[str, str]:
    """Pick the URL the agent should PUT uploads to. Returns ``(base_url, source)``.

    Resolution order (first match wins):

    1. ``env`` — ``OPENVIKING_PUBLIC_BASE_URL`` environment variable. Operator-set,
       always wins.
    2. ``config`` — ``ServerConfig.public_base_url``. Operator-set baseline in ov.conf.
    3. ``forwarded`` — ``X-Forwarded-Host`` (+ ``X-Forwarded-Proto``) from the request.
       Set by reverse proxies (nginx, ALB, ingress controllers, MCP proxies). Reliable
       when the proxy chain forwards these headers, which is the standard default.
    4. ``host`` — the raw ``Host`` header from a direct connection. Reliable for
       same-host MCP clients (e.g. local Claude Code talking to localhost server).
    5. ``listen`` — ``http://{listen_host}:{listen_port}`` last-resort fallback.
       Only produces an agent-reachable URL when the server is bound to a routable
       address; commonly wrong behind reverse proxies.

    Sources 1 and 2 are "explicit" — operator vouched for the URL. Sources 3-5 are
    inferred and may be wrong when the proxy chain doesn't forward request headers.
    Callers should append a "set OPENVIKING_PUBLIC_BASE_URL if upload fails" hint
    in that case.
    """
    env_url = os.environ.get("OPENVIKING_PUBLIC_BASE_URL")
    if env_url:
        return env_url.rstrip("/"), "env"
    config = get_server_config()
    if config is not None and config.public_base_url:
        return config.public_base_url.rstrip("/"), "config"

    url_info = _request_url_ctx.get()
    if url_info:
        xfh = url_info.get("x_forwarded_host")
        xfp = url_info.get("x_forwarded_proto")
        host_hdr = url_info.get("host")
        if xfh:
            proto = xfp or "https"
            return f"{proto}://{xfh}", "forwarded"
        if host_hdr:
            return f"http://{host_hdr}", "host"

    if config is not None:
        return f"http://{config.host}:{config.port}", "listen"
    return "http://127.0.0.1:1933", "listen"


@mcp.tool()
async def add_resource(
    path: str = "",
    temp_file_id: str = "",
    description: str = "",
) -> str:
    """Add a resource to OpenViking. Asynchronous — processing happens in the background.

    Two ways to invoke:

    1. Remote URL: pass ``path`` set to an http(s)://, git@, ssh://, or git:// URL.
       Returns a success message immediately.

    2. Local file: pass ``path`` set to a local filesystem path (e.g. ``/tmp/foo.pdf``).
       The response will NOT be a success message — it will be a multi-step upload
       instruction. Follow the instructions: HTTP-upload the file bytes to the URL
       given in the response, then call this tool again with ``temp_file_id`` set to
       the value the response gave you (and omit ``path``).
    """
    service = get_service()
    ctx = _get_ctx()

    # Branch 1: ingest by temp_file_id (second leg of progressive upload, or REST-style)
    if temp_file_id:
        upload_temp_dir = get_openviking_config().storage.get_upload_temp_dir()
        try:
            resolved_path, allow_local, original_filename = _resolve_temp_or_path(
                path=None,
                temp_file_id=temp_file_id,
                upload_temp_dir=upload_temp_dir,
                account_id=ctx.user.account_id,
                user_id=ctx.user.user_id,
            )
        except (PermissionDeniedError, InvalidArgumentError) as exc:
            return f"Error: {exc}"
        try:
            result = await service.resources.add_resource(
                path=resolved_path,
                ctx=ctx,
                reason=description,
                source_name=original_filename,
                wait=False,
                allow_local_path_resolution=allow_local,
                enforce_public_remote_targets=True,
            )
        except Exception as exc:
            return f"Error adding resource: {exc}"
        root_uri = result.get("root_uri", "")
        return (
            f"Resource added: {root_uri}"
            if root_uri
            else "Resource added (processing in background)."
        )

    if not path:
        return "Error: provide either 'path' (remote URL or local file) or 'temp_file_id'."

    # Branch 2: agent passed a temp_file_id-shaped string as `path` — guide them
    if TEMP_FILE_ID_RE.match(path):
        return (
            f"Error: '{path}' looks like a temp_file_id, not a path. "
            f'Pass it as the temp_file_id kwarg: add_resource(temp_file_id="{path}")'
        )

    # Branch 3: remote URL — same flow as before
    if is_remote_resource_source(path):
        try:
            result = await service.resources.add_resource(
                path=path,
                ctx=ctx,
                reason=description,
                wait=False,
                enforce_public_remote_targets=True,
            )
        except Exception as exc:
            return f"Error adding resource: {exc}"
        root_uri = result.get("root_uri", "")
        return (
            f"Resource added: {root_uri}"
            if root_uri
            else "Resource added (processing in background)."
        )

    # Branch 4: local path — mint token, return upload instruction
    server_config = get_server_config()
    ttl_seconds = (
        server_config.upload_signed_ttl_seconds
        if server_config is not None
        else _DEFAULT_UPLOAD_TTL_SECONDS
    )
    suffix = Path(path).suffix
    minted_tfid = f"upload_{uuid.uuid4().hex}{suffix}"

    token, expires_at = upload_token_store.issue(
        ctx.user.account_id,
        ctx.user.user_id,
        minted_tfid,
        ttl_seconds=ttl_seconds,
    )
    base_url, url_source = _resolve_public_base_url()
    upload_url = (
        f"{base_url}/api/v1/resources/temp_upload_signed"
        f"?token={quote(token, safe='')}&temp_file_id={quote(minted_tfid, safe='')}"
    )
    expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(timespec="seconds")
    minutes = max(1, ttl_seconds // 60)

    prose = (
        "Local file detected — upload required before this resource can be ingested.\n"
        "\n"
        'Step 1. HTTP POST the file bytes (multipart/form-data, field name "file") to:\n'
        "\n"
        f"  {upload_url}\n"
        "\n"
        "Step 2. After the upload returns 200, call this tool again:\n"
        "\n"
        f'  add_resource(temp_file_id="{minted_tfid}")\n'
        "\n"
        f"This upload URL expires in ~{minutes} minutes ({expires_iso})."
    )

    if url_source not in ("env", "config"):
        prose += (
            "\n\n"
            "Note for the user: this upload URL was auto-detected from the incoming "
            "request because OPENVIKING_PUBLIC_BASE_URL is not set on the server. "
            "If Step 1 fails (connection refused, wrong host, TLS error), ask the "
            "server operator to set OPENVIKING_PUBLIC_BASE_URL to the agent-facing "
            "URL of the OpenViking server (e.g. via docker-compose `environment:` "
            "or systemd unit) and retry."
        )

    return prose


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
            "MCP endpoint ready (9 tools: search, read, list, store, add_resource, grep, glob, forget, health)"
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
