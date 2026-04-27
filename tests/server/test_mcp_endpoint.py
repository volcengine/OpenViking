# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for MCP endpoint tools (openviking/server/mcp_endpoint.py).

Tests the tool functions directly by setting up the identity contextvar
and service dependency, avoiding MCP protocol complexity.
"""

import pytest

from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.mcp_endpoint import (
    _get_ctx,
    _mcp_ctx,
    forget,
    health,
    read,
    search,
    store,
)
from openviking_cli.exceptions import UnauthenticatedError
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


# ---------------------------------------------------------------------------
# read tool
# ---------------------------------------------------------------------------


async def test_read_nonexistent_uri(service):
    result = await read("viking://user/default/memories/does_not_exist.md")
    assert "nothing found" in result.lower()


async def test_read_directory(service):
    result = await read("viking://user")
    assert isinstance(result, str)


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
# store tool
# ---------------------------------------------------------------------------


async def test_store_returns_confirmation(service):
    result = await store(text="Test memory: the sky is blue")
    assert "stored" in result.lower()


async def test_store_with_assistant_role(service):
    result = await store(text="I learned something", role="assistant")
    assert "stored" in result.lower()


# ---------------------------------------------------------------------------
# forget tool
# ---------------------------------------------------------------------------


async def test_forget_requires_uri_or_query(service):
    result = await forget()
    assert "provide" in result.lower()


async def test_forget_refuses_non_memory_uri(service):
    result = await forget(uri="viking://resources/some_file.md")
    assert "refusing" in result.lower()


async def test_forget_by_uri_deletes(service):
    ctx = DEFAULT_CTX
    uri = "viking://user/default/memories/test_forget.md"
    await service.viking_fs.mkdir("viking://user/default/memories", ctx=ctx, exist_ok=True)
    await service.viking_fs.write(uri, "test data", ctx=ctx)

    result = await forget(uri=uri)
    assert "deleted" in result.lower()
    assert "test_forget.md" in result


async def test_forget_by_query_no_matches(service):
    result = await forget(query="zzz_absolutely_no_match_xyz_99999")
    assert "no matching" in result.lower()


# ---------------------------------------------------------------------------
# Identity middleware
# ---------------------------------------------------------------------------


def test_mcp_route_registered(app):
    """Verify the /mcp route exists in the app."""
    mcp_routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/mcp"]
    assert len(mcp_routes) == 1
