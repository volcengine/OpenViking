# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for MCP endpoint tools (openviking/server/mcp_endpoint.py).

Tests the tool functions directly by setting up the identity contextvar
and service dependency, avoiding MCP protocol complexity.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from starlette.routing import Route

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.dependencies import set_service
from openviking.server.identity import AuthMode, RequestContext, Role
from openviking.server.mcp_endpoint import (
    StoreMessage,
    _get_ctx,
    _IdentityASGIMiddleware,
    _mcp_ctx,
    add_resource,
    cancel_watch,
    forget,
    glob,
    grep,
    health,
    list_watches,
    read,
    recall,
    remember,
    search,
)
from openviking.server.mcp_endpoint import ls as list_tool
from openviking_cli.exceptions import FailedPreconditionError, UnauthenticatedError
from openviking_cli.session.user_id import UserIdentifier

DEFAULT_CTX = RequestContext(
    user=UserIdentifier.the_default_user("test_user"),
    role=Role.ROOT,
)


@pytest.fixture(autouse=True)
def _set_mcp_identity(service):
    """Set identity contextvar and wire service for all tests."""
    set_service(service)
    token = _mcp_ctx.set(DEFAULT_CTX)
    yield
    _mcp_ctx.reset(token)


# ---------------------------------------------------------------------------
# _get_ctx
# ---------------------------------------------------------------------------


def test_get_ctx_returns_set_context():
    ctx = _get_ctx()
    assert ctx.user.user_id == "test_user"


def test_get_ctx_raises_when_unset():
    token = _mcp_ctx.set(None)
    try:
        with pytest.raises(UnauthenticatedError):
            _get_ctx()
    finally:
        _mcp_ctx.reset(token)


# ---------------------------------------------------------------------------
# health tool
# ---------------------------------------------------------------------------


async def test_health_returns_healthy(service):
    result = await health()
    assert "healthy" in result.lower()
    assert "VikingFS" in result


async def test_health_returns_unhealthy_when_no_service(monkeypatch):
    monkeypatch.setattr(
        "openviking.server.mcp_endpoint.get_service",
        lambda: (_ for _ in ()).throw(RuntimeError("not initialized")),
    )
    result = await health()
    assert "unhealthy" in result.lower()


# ---------------------------------------------------------------------------
# search tool
# ---------------------------------------------------------------------------


async def test_search_no_results(service):
    result = await search(query="zzz_nonexistent_query_xyz_12345")
    assert result == "No matching context found."


async def test_search_returns_formatted_results(service, client_with_resource):
    _, root_uri = client_with_resource
    result = await search(query="resource management semantic search", limit=3)
    assert "Found" in result or "No matching" in result


async def test_search_with_target_uri(service):
    result = await search(query="test", target_uri="viking://resources", limit=3)
    assert isinstance(result, str)


async def test_search_respects_min_score(service):
    result = await search(query="test", min_score=0.35)
    assert isinstance(result, str)


async def test_search_tools_expose_only_context_type_parameter():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    for tool_name in ("find", "search"):
        properties = tools[tool_name].inputSchema["properties"]
        assert "context_type" in properties
        assert "filter" not in properties


async def test_tool_schemas_are_portable():
    """Every advertised schema node must carry an explicit type, with no
    anyOf/$ref/$defs — strict function-calling APIs (e.g. Gemini's OpenAPI
    subset) reject schemas that lack these guarantees."""

    def assert_portable(node, path):
        if not isinstance(node, dict):
            return
        assert "anyOf" not in node, f"{path}: anyOf not portable"
        assert "$ref" not in node, f"{path}: $ref not portable"
        assert "$defs" not in node, f"{path}: $defs not portable"
        assert "type" in node, f"{path}: missing explicit type"
        assert node.get("default", "") is not None, f"{path}: null default"
        for key, sub in node.get("properties", {}).items():
            assert_portable(sub, f"{path}.{key}")
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                assert_portable(node[key], f"{path}.{key}")

    tools = await mcp_endpoint.mcp.list_tools()
    assert tools
    for tool in tools:
        assert_portable(tool.inputSchema, tool.name)


def test_portable_schema_collapses_unions():
    collapsed = mcp_endpoint._portable_schema(
        {
            "anyOf": [
                {"type": "string"},
                {"items": {"type": "string"}, "type": "array"},
                {"type": "null"},
            ],
            "default": None,
            "description": "one or many",
        }
    )
    assert collapsed == {
        "type": "array",
        "items": {"type": "string"},
        "description": "one or many",
    }


def test_portable_schema_inlines_refs():
    inlined = mcp_endpoint._portable_schema(
        {
            "$defs": {"Item": {"properties": {"name": {"type": "string"}}, "type": "object"}},
            "items": {"$ref": "#/$defs/Item"},
            "type": "array",
        }
    )
    assert inlined == {
        "items": {"properties": {"name": {"type": "string"}}, "type": "object"},
        "type": "array",
    }


async def test_find_tool_calls_lightweight_find(service, monkeypatch):
    captured = {}

    async def fake_find(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(memories=[], resources=[], skills=[])

    monkeypatch.setattr(service.search, "find", fake_find)

    result = await mcp_endpoint.find(
        query="fast lookup",
        target_uri="viking://resources",
        limit=2,
        min_score=0.2,
        context_type=["memory", "resource"],
    )

    assert result == "No matching context found."
    assert captured["query"] == "fast lookup"
    assert captured["ctx"] == DEFAULT_CTX
    assert captured["target_uri"] == "viking://resources"
    assert captured["limit"] == 2
    assert captured["score_threshold"] == 0.2
    assert captured["filter"] == {
        "op": "must",
        "field": "context_type",
        "conds": ["memory", "resource"],
    }


async def test_search_tool_calls_context_aware_search_with_session(service, monkeypatch):
    captured = {}
    session = SimpleNamespace(load_called=False)

    async def load():
        session.load_called = True

    session.load = load

    def session_factory(ctx, session_id):
        captured["session_factory_ctx"] = ctx
        captured["session_id"] = session_id
        return session

    async def fake_search(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(memories=[], resources=[], skills=[])

    async def fail_find(**kwargs):
        raise AssertionError("MCP search should call service.search.search, not find")

    monkeypatch.setattr(service.sessions, "session", session_factory)
    monkeypatch.setattr(service.search, "search", fake_search)
    monkeypatch.setattr(service.search, "find", fail_find)

    result = await search(
        query="deep lookup",
        target_uri="viking://resources",
        session_id="session-1",
        limit=4,
        min_score=0.1,
        context_type="skill",
    )

    assert result == "No matching context found."
    assert session.load_called is True
    assert captured["session_factory_ctx"] == DEFAULT_CTX
    assert captured["session_id"] == "session-1"
    assert captured["query"] == "deep lookup"
    assert captured["ctx"] == DEFAULT_CTX
    assert captured["target_uri"] == "viking://resources"
    assert captured["session"] == session
    assert captured["limit"] == 4
    assert captured["score_threshold"] == 0.1
    assert captured["filter"] == {
        "op": "must",
        "field": "context_type",
        "conds": ["skill"],
    }


async def test_recall_tool_returns_type_quota_memory_groups(service, monkeypatch):
    async def fake_find(**kwargs):
        if kwargs["target_uri"].endswith("/events"):
            return SimpleNamespace(
                memories=[
                    SimpleNamespace(
                        uri="viking://user/test_user/memories/events/e.md",
                        score=0.9,
                        abstract="event abstract",
                    )
                ]
            )
        return SimpleNamespace(memories=[])

    async def fake_read(uri, **kwargs):
        del uri, kwargs
        return "Summary: MCP recall event.\n2026-07-06 ChatLog: details"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read", fake_read)

    result = await recall(
        query="what happened",
        quotas={"events": 1, "entities": 0, "preferences": 0, "experiences": 0},
        max_chars=200,
        min_score=0.1,
    )

    assert '<memory_group type="events"' in result
    assert "MCP recall event." in result


async def test_mcp_middleware_sets_actor_peer_context():
    async def downstream(scope, receive, send):
        ctx = _get_ctx()
        assert ctx.actor_peer_id == "peer-a"
        response = httpx.Response(200, json={"ok": True})
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": response.content})

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.routes.append(Route("/mcp", endpoint=_IdentityASGIMiddleware(downstream), methods=["POST"]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"X-OpenViking-Actor-Peer": "peer-a"},
        )

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("headers", "expected_api_key"),
    [
        ({"X-API-Key": "api-key-secret"}, "api-key-secret"),
        ({"Authorization": "Bearer bearer-secret"}, "bearer-secret"),
        ({}, None),
    ],
)
async def test_mcp_middleware_propagates_request_api_key(headers, expected_api_key):
    async def downstream(scope, receive, send):
        assert _get_ctx().api_key == expected_api_key
        response = httpx.Response(200, json={"ok": True})
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": response.content})

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.routes.append(Route("/mcp", endpoint=_IdentityASGIMiddleware(downstream), methods=["POST"]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers=headers,
        )

    assert response.status_code == 200


async def test_mcp_middleware_rejects_invalid_actor_peer_header():
    async def downstream(scope, receive, send):
        raise AssertionError("invalid actor peer header should not reach downstream app")

    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: AuthMode.DEV)
    app.state.auth_plugin = DevAuthPlugin()
    app.routes.append(Route("/mcp", endpoint=_IdentityASGIMiddleware(downstream), methods=["POST"]))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"X-OpenViking-Actor-Peer": "bad/peer"},
        )

    assert response.status_code == 400
    assert "path separators" in response.text


# ---------------------------------------------------------------------------
# read tool
# ---------------------------------------------------------------------------


async def test_read_nonexistent_uri(service):
    result = await read("viking://user/default/memories/does_not_exist.md")
    assert "nothing found" in result.lower()


async def test_read_batch(service):
    result = await read(
        [
            "viking://user/default/memories/does_not_exist_1.md",
            "viking://user/default/memories/does_not_exist_2.md",
        ]
    )
    assert "===" in result
    assert "nothing found" in result.lower()


# ---------------------------------------------------------------------------
# list tool
# ---------------------------------------------------------------------------


async def test_list_root(service):
    result = await list_tool("viking://user")
    assert isinstance(result, str)


async def test_list_empty_dir(service):
    ctx = DEFAULT_CTX
    await service.viking_fs.mkdir(
        "viking://user/default/memories/empty_test", ctx=ctx, exist_ok=True
    )
    result = await list_tool("viking://user/default/memories/empty_test")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# store tool
# ---------------------------------------------------------------------------


async def test_store_single_message(service):
    result = await remember(messages=[StoreMessage(role="user", content="The sky is blue")])
    assert "stored" in result.lower()
    assert "1 message" in result


async def test_store_batch_messages(service):
    result = await remember(
        messages=[
            StoreMessage(role="user", content="Remember my favorite color is blue"),
            StoreMessage(role="assistant", content="Noted, your favorite color is blue."),
        ]
    )
    assert "stored" in result.lower()
    assert "2 message" in result


async def test_store_does_not_autofill_peer_id_from_ctx(service, monkeypatch):
    """MCP store should not create synthetic peer_id values."""
    from openviking.session.session import Session

    captured: list[tuple[str, str | None]] = []
    original = Session.add_message

    def _spy(self, role, parts, peer_id=None, created_at=None):
        captured.append((role, peer_id))
        return original(self, role, parts, peer_id=peer_id, created_at=created_at)

    monkeypatch.setattr(Session, "add_message", _spy)

    await remember(
        messages=[
            StoreMessage(role="user", content="user msg"),
            StoreMessage(role="assistant", content="assistant msg"),
        ]
    )

    assert captured == [
        ("user", None),
        ("assistant", None),
    ]


async def test_store_skips_empty_message_content(service, monkeypatch):
    class FakeSession:
        def __init__(self):
            self.messages = []

        def add_message(self, role, parts, peer_id=None, created_at=None):
            self.messages.append((role, parts, peer_id, created_at))

    fake_session = FakeSession()
    monkeypatch.setattr(service.sessions, "get", AsyncMock(return_value=fake_session))
    monkeypatch.setattr(service.sessions, "commit_async", AsyncMock())

    result = await remember(
        messages=[
            StoreMessage(role="user", content=""),
            StoreMessage(role="assistant", content="Noted."),
        ]
    )

    assert "2 message" in result
    assert len(fake_session.messages) == 1
    role, parts, peer_id, created_at = fake_session.messages[0]
    assert role == "assistant"
    assert parts[0].text == "Noted."
    assert peer_id is None
    assert created_at is None
    service.sessions.commit_async.assert_awaited_once()


# ---------------------------------------------------------------------------
# add_resource tool
# ---------------------------------------------------------------------------


async def test_add_resource_local_path_returns_upload_instruction(service):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    result = await add_resource(path="/tmp/sample_local_file_xyz.pdf")
    assert "local file detected" in result.lower()
    # Single-step flow: POST the file and the server auto-ingests. No second call, no
    # temp_file_id handshake exposed to the agent.
    assert "/api/v1/resources/temp_upload?token=" in result
    assert "temp_upload_signed" not in result
    assert "automatically" in result.lower()
    assert "temp_file_id" not in result
    # Default fixture sets neither env nor config.public_base_url → URL is auto-inferred
    # and the troubleshooting hint must appear.
    assert "OPENVIKING_PUBLIC_BASE_URL" in result
    upload_token_store.clear()


async def test_add_resource_local_path_uses_env_var_when_set(service, monkeypatch):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://my-ov.example.com")
    result = await add_resource(path="/tmp/x.pdf")
    assert "https://my-ov.example.com/api/v1/resources/temp_upload?token=" in result
    # Explicit source → no troubleshooting hint
    assert "OPENVIKING_PUBLIC_BASE_URL is not set" not in result
    upload_token_store.clear()


async def test_add_resource_local_path_uses_config_when_env_unset(service, monkeypatch):
    from openviking.server.config import ServerConfig
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setattr(
        "openviking.server.dependencies._server_config",
        ServerConfig(public_base_url="https://configured.example.com"),
    )

    result = await add_resource(path="/tmp/x.pdf")
    assert "https://configured.example.com/api/v1/resources/temp_upload?token=" in result
    assert "OPENVIKING_PUBLIC_BASE_URL is not set" not in result
    upload_token_store.clear()


async def test_add_resource_local_path_infers_from_x_forwarded_headers(service, monkeypatch):
    from openviking.server.mcp_endpoint import _request_url_ctx
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)

    token = _request_url_ctx.set(
        {
            "x_forwarded_proto": "https",
            "x_forwarded_host": "ov.public.example.com",
            "host": "internal:1933",
        }
    )
    try:
        result = await add_resource(path="/tmp/x.pdf")
    finally:
        _request_url_ctx.reset(token)

    assert "https://ov.public.example.com/api/v1/resources/temp_upload?token=" in result
    # Inferred → hint must appear
    assert "OPENVIKING_PUBLIC_BASE_URL" in result
    upload_token_store.clear()


async def test_add_resource_temp_file_id_lookalike_in_path_is_rejected(service):
    result = await add_resource(path="upload_abc123.pdf")
    assert "looks like a temp_file_id" in result.lower()
    assert 'temp_file_id="upload_abc123.pdf"' in result


async def test_add_resource_neither_path_nor_temp_file_id(service):
    result = await add_resource()
    assert "error" in result.lower()
    assert "path" in result.lower() or "temp_file_id" in result.lower()


async def test_add_resource_remote_url_is_ingested(service, monkeypatch):
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured["path"] = path
        captured["enforce_public_remote_targets"] = kwargs.get("enforce_public_remote_targets")
        return {"root_uri": "viking://resources/test_remote"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)
    result = await add_resource(path="https://example.com/x.md")
    assert "Resource added" in result
    assert captured["path"] == "https://example.com/x.md"
    assert captured["enforce_public_remote_targets"] is True


async def test_add_resource_remote_async_result_exposes_task_id(service, monkeypatch):
    async def fake_add_resource(*, path, ctx, **kwargs):
        return {
            "status": "accepted",
            "task_id": "ov-task-123",
            "connector_task_key": "connector-task-456",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    result = await add_resource(path="tos://bucket/docs")

    assert "task_id: ov-task-123" in result
    assert "processing in background" in result


async def test_add_resource_remote_parent_is_forwarded(service, monkeypatch):
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured.update(kwargs)
        return {"task_id": "ov-task-123"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    await add_resource(
        path="tos://bucket/docs",
        parent="viking://resources/imports",
    )

    assert captured["parent"] == "viking://resources/imports"
    assert captured["to"] is None


async def test_add_resource_temp_file_id_branch_resolves_and_ingests(
    service, upload_temp_dir, monkeypatch
):
    """When temp_file_id is supplied, MCP resolves via TempUploadStore and ingests."""
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()

    # Drop a file in the flat-local layout used by TempUploadStore._resolve_local.
    tfid = "upload_abcdef123.md"
    target = upload_temp_dir / tfid
    target.write_text("hello mcp")

    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured["path"] = path
        captured["allow_local_path_resolution"] = kwargs.get("allow_local_path_resolution")
        return {"root_uri": "viking://resources/from_tfid"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    result = await add_resource(temp_file_id=tfid)
    assert "Resource added" in result
    assert captured["path"] == str(target.resolve())
    assert captured["allow_local_path_resolution"] is True
    upload_token_store.clear()


async def test_add_resource_temp_file_id_ingest_error_is_surfaced(
    service, upload_temp_dir, monkeypatch
):
    """add_resource returns a business-error dict without raising; MCP must surface it."""
    tfid = "upload_deadbeef.md"
    (upload_temp_dir / tfid).write_text("junk")

    async def failing_add_resource(*, path, ctx, **kwargs):
        return {"status": "error", "errors": ["parse failed"]}

    monkeypatch.setattr(service.resources, "add_resource", failing_add_resource)

    result = await add_resource(temp_file_id=tfid)
    assert "Error adding resource" in result
    assert "parse failed" in result
    assert "Resource added" not in result


async def test_add_resource_watch_without_to_is_forwarded(service, monkeypatch):
    """watch_interval > 0 may omit `to`; the service binds to the created root_uri."""
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return {"root_uri": "viking://resources/foo"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    result = await add_resource(
        path="https://example.com/foo",
        watch_interval=1440,
    )
    assert "Resource added" in result
    assert captured["path"] == "https://example.com/foo"
    assert captured["to"] is None
    assert captured["watch_interval"] == 1440


async def test_add_resource_rejects_negative_watch_interval(service):
    """watch_interval < 0 is rejected at the MCP boundary, even when `to` is given.

    Without this guard, a negative value would bypass watch creation and be
    forwarded into the service layer with cancellation-like semantics.
    """
    result = await add_resource(
        path="https://example.com/foo",
        watch_interval=-1,
        to="viking://resources/test/neg",
    )
    assert "error" in result.lower()
    assert "watch_interval must be >= 0" in result


# ---------------------------------------------------------------------------
# list_watches / cancel_watch tools
# ---------------------------------------------------------------------------


async def _seed_watch(service, to_uri="viking://resources/test/foo"):
    wm = service.watch_scheduler.watch_manager
    return await wm.create_task(
        path="https://example.com/foo",
        account_id=DEFAULT_CTX.account_id,
        user_id=DEFAULT_CTX.user.user_id,
        original_role="root",
        to_uri=to_uri,
        watch_interval=1440.0,
    )


async def test_list_watches_empty(service):
    result = await list_watches()
    assert "no watch" in result.lower()


async def test_list_watches_with_seed(service):
    task = await _seed_watch(service, to_uri="viking://resources/test/list")
    result = await list_watches()
    assert task.to_uri in result
    assert "active" in result.lower()
    assert "1440" in result


async def test_cancel_watch_by_uri(service):
    task = await _seed_watch(service, to_uri="viking://resources/test/cancel")
    result = await cancel_watch(to_uri=task.to_uri)
    assert "cancelled" in result.lower()
    # Verify it's actually gone
    follow_up = await list_watches()
    assert task.to_uri not in follow_up


async def test_cancel_watch_not_found(service):
    result = await cancel_watch(to_uri="viking://resources/never/existed")
    assert "no watch task found" in result.lower()


# ---------------------------------------------------------------------------
# forget tool
# ---------------------------------------------------------------------------


async def test_forget_by_uri_deletes_memory(service):
    ctx = DEFAULT_CTX
    uri = "viking://user/default/memories/test_forget.md"
    await service.viking_fs.mkdir("viking://user/default/memories", ctx=ctx, exist_ok=True)
    await service.viking_fs.write(uri, "test data", ctx=ctx)

    result = await forget(uri=uri)
    assert "deleted" in result.lower()
    assert "test_forget.md" in result


async def test_forget_by_uri_deletes_resource(service):
    """forget should work on any viking:// URI, not just memories."""
    ctx = DEFAULT_CTX
    uri = "viking://resources/test_forget_resource.md"
    await service.viking_fs.mkdir("viking://resources", ctx=ctx, exist_ok=True)
    await service.viking_fs.write(uri, "resource data", ctx=ctx)

    result = await forget(uri=uri)
    assert "deleted" in result.lower()


async def test_forget_directory_without_recursive_fails(service):
    ctx = DEFAULT_CTX
    dir_uri = "viking://resources/test_forget_dir"
    child_uri = f"{dir_uri}/child.md"
    await service.viking_fs.mkdir(dir_uri, ctx=ctx, exist_ok=True)
    await service.viking_fs.write(child_uri, "child data", ctx=ctx)

    with pytest.raises(FailedPreconditionError):
        await forget(uri=dir_uri)


async def test_forget_directory_with_recursive_succeeds(service):
    ctx = DEFAULT_CTX
    dir_uri = "viking://resources/test_forget_dir_recursive"
    child_uri = f"{dir_uri}/child.md"
    await service.viking_fs.mkdir(dir_uri, ctx=ctx, exist_ok=True)
    await service.viking_fs.write(child_uri, "child data", ctx=ctx)

    result = await forget(uri=dir_uri, recursive=True)
    assert "deleted" in result.lower()


# ---------------------------------------------------------------------------
# grep tool
# ---------------------------------------------------------------------------


async def test_grep_no_matches(service):
    result = await grep(uri="viking://resources", pattern="zzz_no_match_xyz_99999")
    assert "No matches found" in result


async def test_grep_single_pattern(service, client_with_resource):
    _, root_uri = client_with_resource
    result = await grep(uri=root_uri, pattern=".*")
    assert isinstance(result, str)


async def test_grep_multiple_patterns(service):
    result = await grep(uri="viking://resources", pattern=["pattern_a_xyz", "pattern_b_xyz"])
    assert "No matches found" in result
    assert "pattern_a_xyz" in result
    assert "pattern_b_xyz" in result


async def test_grep_case_insensitive(service):
    result = await grep(uri="viking://resources", pattern="TEST", case_insensitive=True)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# glob tool
# ---------------------------------------------------------------------------


async def test_glob_no_matches(service):
    result = await glob(pattern="**/zzz_nonexistent_*.xyz")
    assert "No files found" in result


async def test_glob_match_all_md(service, client_with_resource):
    _, root_uri = client_with_resource
    result = await glob(pattern="**/*.md", uri=root_uri)
    assert isinstance(result, str)


async def test_glob_with_uri_scope(service):
    result = await glob(pattern="**/*", uri="viking://resources")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_mcp_route_registered(app):
    """Verify the /mcp route exists in the app."""
    mcp_routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/mcp"]
    assert len(mcp_routes) == 1
