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
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Literal, Optional
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
from openviking.server.local_input_guard import (
    TEMP_FILE_ID_RE,
    is_remote_resource_source,
)
from openviking.server.temp_upload_store import TempUploadStore
from openviking.server.upload_token_store import upload_token_store
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


def _scope_to_origin(scope: Scope) -> Optional[str]:
    """Derive the public-facing origin (scheme://host) from an ASGI scope.

    Resolution order matches openviking.server.oauth.router._public_origin:
      1. ``OPENVIKING_PUBLIC_BASE_URL`` environment variable
      2. ``app.state.oauth_config.issuer`` (if OAuth enabled)
      3. ``X-Forwarded-Proto`` / ``X-Forwarded-Host``
      4. scope's own scheme + Host header
    """
    import os as _os

    env_value = _os.environ.get("OPENVIKING_PUBLIC_BASE_URL", "").strip()
    if env_value:
        return env_value.rstrip("/")

    app = scope.get("app")
    if app is not None:
        cfg = getattr(app.state, "oauth_config", None)
        configured = getattr(cfg, "issuer", None) if cfg else None
        if configured:
            return configured.rstrip("/")

    headers = {
        k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
    }
    proto = headers.get("x-forwarded-proto") or scope.get("scheme") or "http"
    proto = proto.split(",", 1)[0].strip()
    host = headers.get("x-forwarded-host") or headers.get("host")
    if not host:
        server = scope.get("server")
        if isinstance(server, (list, tuple)) and len(server) >= 2:
            host = f"{server[0]}:{server[1]}" if server[1] else str(server[0])
    if not host:
        return None
    host = host.split(",", 1)[0].strip()
    return f"{proto}://{host}"


def _oauth_enabled(scope: Scope) -> bool:
    """Return True if app.state has an oauth_provider (i.e. OAuth is configured)."""
    app = scope.get("app")
    if app is None:
        return False
    return getattr(app.state, "oauth_provider", None) is not None


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
            headers: dict[str, str] = {}
            # When OAuth is enabled and the request is unauthenticated, advertise
            # the OAuth 2.0 protected resource metadata so MCP clients (Claude.ai,
            # Claude Desktop, etc.) can auto-discover the authorization server
            # per RFC 9728 §5.1.
            if status == 401 and _oauth_enabled(scope):
                origin = _scope_to_origin(scope)
                if origin:
                    headers["WWW-Authenticate"] = (
                        f'Bearer resource_metadata="{origin}/.well-known/oauth-protected-resource"'
                    )
            resp = JSONResponse(
                {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": str(exc)}},
                status_code=status,
                headers=headers,
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
# MCP server tools (aligned with vikingbot/agent/tools/ov_file.py)
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "openviking",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


# -- find / search ---------------------------------------------------------


@mcp.tool()
async def find(
    query: str,
    target_uri: str = "",
    limit: int = 10,
    min_score: float = 0.35,
    level: Optional[List[int]] = None,
) -> str:
    """Fast semantic retrieval without session context. Returns ranked memories, resources, and skills with URI, abstract, and score."""
    service = get_service()
    result = await service.search.find(
        query=query,
        ctx=_get_ctx(),
        target_uri=target_uri,
        limit=limit,
        score_threshold=min_score,
        level=level,
    )
    return _format_search_result(result)


@mcp.tool()
async def search(
    query: str,
    target_uri: str = "",
    session_id: Optional[str] = None,
    limit: int = 10,
    min_score: float = 0.35,
    level: Optional[List[int]] = None,
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
        level=level,
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


_DEFAULT_UPLOAD_TTL_SECONDS = 600


def _resolve_public_base_url() -> tuple[str, str]:
    """Pick the URL the agent should POST uploads to. Returns ``(base_url, source)``.

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
        # X-Forwarded-Host / -Proto can be comma-separated lists when the request
        # crosses multiple proxy hops. Take the first (left-most original-client)
        # value, matching the normalization in openviking.server.oauth.router.
        def _first(value: Optional[str]) -> Optional[str]:
            if not value:
                return None
            head = value.split(",", 1)[0].strip()
            return head or None

        xfh = _first(url_info.get("x_forwarded_host"))
        xfp = _first(url_info.get("x_forwarded_proto"))
        host_hdr = _first(url_info.get("host"))
        if xfh:
            proto = xfp or "https"
            return f"{proto}://{xfh}", "forwarded"
        if host_hdr:
            return f"http://{host_hdr}", "host"

    if config is not None:
        return f"http://{config.host}:{config.port}", "listen"
    return "http://127.0.0.1:1933", "listen"


_WATCH_REQUIRES_TO_HINT = (
    "watch_interval > 0 requires `to` to be specified (the stable target URI to refresh into). "
    "Pick a deterministic URI under viking://resources/. For example:\n"
    "  - https://github.com/<org>/<repo>  -> to='viking://resources/<org>/<repo>'\n"
    "  - https://example.com/docs/api     -> to='viking://resources/example.com/docs/api'\n"
    "Tip: call add_resource without watch_interval first, observe the returned URI, "
    "then call again with watch_interval=<minutes> and to=<that URI>."
)


@mcp.tool()
async def add_resource(
    path: str = "",
    temp_file_id: str = "",
    description: str = "",
    watch_interval: float = 0,
    to: str = "",
) -> str:
    """Add a resource to OpenViking. Asynchronous — processing happens in the background.

    Three ways to invoke:

    1. Remote URL: pass ``path`` set to an http(s)://, git@, ssh://, or git:// URL.
       Returns a success message immediately. Supports ``watch_interval`` + ``to`` for
       auto-refresh subscriptions.

    2. Local file: pass ``path`` set to a local filesystem path (e.g. ``/tmp/foo.pdf``).
       The response is NOT a success message — it's a multi-step upload instruction.
       HTTP POST the file to the URL the response gives you, read ``temp_file_id`` from
       the upload response body, then call this tool again with that ``temp_file_id``.

    3. Re-call after upload: pass ``temp_file_id`` set to the value the signed upload
       response returned. Omit ``path``. The server resolves the file via TempUploadStore
       and ingests it.

    Args:
        path: Remote URL or local filesystem path.
        temp_file_id: Server-minted upload id from a prior signed upload. Either
            ``path`` or ``temp_file_id`` is required.
        description: Optional human-readable reason for adding the resource.
        watch_interval: Auto-refresh cadence in minutes. 0 (default) = no watch.
            >0 = periodically re-fetch the resource at that cadence (full re-ingest
            each time). Prefer >=1440 (24h) unless the source genuinely changes
            faster — every refresh re-embeds the entire resource. Requires ``to``.
            Only applies to remote-URL invocations.
        to: Target URI under viking://resources/ (e.g.
            "viking://resources/volcengine/OpenViking"). Required when
            watch_interval > 0. Leave empty for one-shot adds — the system will
            auto-derive a URI from the source.
    """
    from openviking.server.local_input_guard import require_remote_resource_source

    service = get_service()
    ctx = _get_ctx()

    if watch_interval < 0:
        return (
            "Error: watch_interval must be >= 0. Use 0 for one-shot add (no watch); "
            "use a positive number of minutes (>=1440 recommended) to subscribe to auto-refresh."
        )

    # Branch 1: ingest by temp_file_id (second leg of progressive upload, or REST-style)
    if temp_file_id:
        from openviking.server.config import ServerConfig

        server_config = get_server_config() or ServerConfig()
        store = TempUploadStore.build(server_config)
        try:
            resolved = await store.resolve_for_consume(temp_file_id, ctx)
        except (PermissionDeniedError, InvalidArgumentError) as exc:
            return f"Error: {exc}"
        try:
            try:
                result = await service.resources.add_resource(
                    path=resolved.local_path,
                    ctx=ctx,
                    reason=description,
                    source_name=resolved.original_filename,
                    wait=False,
                    allow_local_path_resolution=True,
                    enforce_public_remote_targets=True,
                )
            except Exception as exc:
                await store.mark_failed(resolved, ctx)
                return f"Error adding resource: {exc}"
            await store.mark_consumed(resolved, ctx)
        finally:
            await resolved.cleanup()
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
        if watch_interval > 0 and not to:
            return f"Error: {_WATCH_REQUIRES_TO_HINT}"
        try:
            path = require_remote_resource_source(path)
            result = await service.resources.add_resource(
                path=path,
                ctx=ctx,
                to=to or None,
                reason=description,
                wait=False,
                watch_interval=watch_interval,
                enforce_public_remote_targets=True,
            )
        except Exception as exc:
            return f"Error adding resource: {exc}"
        root_uri = result.get("root_uri", "")
        if watch_interval > 0:
            watch_suffix = f" (watch enabled, refresh every {watch_interval:g} minute(s))"
        else:
            watch_suffix = ""
        return (
            f"Resource added: {root_uri}{watch_suffix}"
            if root_uri
            else f"Resource added (processing in background){watch_suffix}."
        )

    # Branch 4: local path — mint token, return upload instruction
    server_config = get_server_config()
    ttl_seconds = (
        server_config.upload_signed_ttl_seconds
        if server_config is not None
        else _DEFAULT_UPLOAD_TTL_SECONDS
    )

    token, expires_at = upload_token_store.issue(
        ctx.user.account_id,
        ctx.user.user_id,
        ctx.user.agent_id,
        ttl_seconds=ttl_seconds,
    )
    base_url, url_source = _resolve_public_base_url()
    upload_url = f"{base_url}/api/v1/resources/temp_upload_signed?token={quote(token, safe='')}"
    expires_iso = datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(timespec="seconds")
    minutes = max(1, ttl_seconds // 60)

    prose = (
        "Local file detected — upload required before this resource can be ingested.\n"
        "\n"
        'Step 1. HTTP POST the file bytes (multipart/form-data, field name "file") to:\n'
        "\n"
        f"  {upload_url}\n"
        "\n"
        '  The response will be JSON: {"temp_file_id": "<id>"}\n'
        "\n"
        "Step 2. Read `temp_file_id` from that response, then call this tool again:\n"
        "\n"
        '  add_resource(temp_file_id="<id from step 1>")\n'
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


# -- watch management ------------------------------------------------------
# MCP exposes the minimum closure: list + cancel. Pause/resume/trigger and
# the unified `update` verb are intentionally NOT exposed — they're either
# low-value for agents or invite unwanted autonomous decisions. Power users
# should reach for the REST API or the `ov task watch *` CLI (`pause`,
# `resume`, `trigger`, `update --interval`, etc.) for those operations.


@mcp.tool()
async def list_watches() -> str:
    """List watch tasks (auto-refresh subscriptions) visible to the current agent.

    Each line shows: target URI, refresh interval (minutes), active/paused status,
    and the next scheduled execution time. Returns "No watch tasks." when empty.
    """
    service = get_service()
    ctx = _get_ctx()
    scheduler = getattr(service, "watch_scheduler", None)
    if scheduler is None or not scheduler.is_running:
        return "Error: Watch scheduler not running"
    wm = scheduler.watch_manager
    if wm is None:
        return "Error: Watch scheduler not running"
    # get_all_tasks does not raise PermissionDeniedError — it silently filters
    # tasks the caller cannot see (watch_manager.py:596-624), so we just
    # accept the filtered list.
    tasks = await wm.get_all_tasks(
        ctx.account_id,
        ctx.user.user_id,
        ctx.role.value,
        active_only=False,
        agent_id=ctx.user.agent_id,
    )
    if not tasks:
        return "No watch tasks."
    lines = []
    for t in tasks:
        status = "active" if t.is_active else "paused"
        nxt = t.next_execution_time.isoformat() if t.next_execution_time else "n/a"
        lines.append(
            f"- {t.to_uri or '(no uri)'}  interval={t.watch_interval:g}m  {status}  next={nxt}"
        )
    return "\n".join(lines)


@mcp.tool()
async def cancel_watch(to_uri: str) -> str:
    """Cancel (delete) a watch task by its target URI.

    The URI must match the watch task's `to` value (e.g. "viking://resources/volcengine/OpenViking").
    To change the cadence or pause temporarily, cancel and re-add with a new watch_interval.
    """
    from openviking.resource import watch_manager as _wm_mod

    service = get_service()
    ctx = _get_ctx()
    scheduler = getattr(service, "watch_scheduler", None)
    if scheduler is None or not scheduler.is_running:
        return "Error: Watch scheduler not running"
    wm = scheduler.watch_manager
    if wm is None:
        return "Error: Watch scheduler not running"
    task = await wm.get_task_by_uri(
        to_uri,
        ctx.account_id,
        ctx.user.user_id,
        ctx.role.value,
        ctx.user.agent_id,
    )
    if task is None:
        return f"No watch task found for {to_uri}"
    try:
        # Return value (bool) is intentionally ignored: delete_task returns
        # False only when the task was removed between our lookup and the
        # delete call (a concurrent cancel from another caller). In that case
        # the post-condition the caller wanted ("no watch on this URI") still
        # holds, so we report the same success message either way. Permission
        # errors still surface via the explicit except below.
        _ = await wm.delete_task(
            task.task_id,
            ctx.account_id,
            ctx.user.user_id,
            ctx.role.value,
            ctx.user.agent_id,
        )
    except _wm_mod.PermissionDeniedError:
        return f"Permission denied for {to_uri}"
    return f"Watch cancelled: {to_uri}"


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
async def forget(uri: str, recursive: bool = False) -> str:
    """Permanently delete a viking:// URI from OpenViking. This is irreversible. Only use when the user explicitly asks to forget or delete something. Always confirm with the user before calling this tool. Use the search tool first to find the exact URI, then pass it here. Set recursive=true only when the user explicitly asks to delete a directory tree."""
    service = get_service()
    ctx = _get_ctx()
    await service.fs.rm(uri, ctx=ctx, recursive=recursive)
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
