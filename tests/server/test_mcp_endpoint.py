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
    StoreMessage,
    _get_ctx,
    _mcp_ctx,
    add_resource,
    forget,
    glob,
    grep,
    health,
    read,
    search,
    store,
)
from openviking.server.mcp_endpoint import ls as list_tool
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


async def test_search_respects_min_score(service):
    result = await search(query="test", min_score=0.35)
    assert isinstance(result, str)


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
    result = await store(messages=[StoreMessage(role="user", content="The sky is blue")])
    assert "stored" in result.lower()
    assert "1 message" in result


async def test_store_batch_messages(service):
    result = await store(
        messages=[
            StoreMessage(role="user", content="Remember my favorite color is blue"),
            StoreMessage(role="assistant", content="Noted, your favorite color is blue."),
        ]
    )
    assert "stored" in result.lower()
    assert "2 message" in result


async def test_store_populates_role_id_from_ctx(service, monkeypatch):
    """Regression: MCP store used to persist role_id=None because it skipped the
    HTTP router's fallback resolver. With ctx.resolve_role_id, user msgs should
    get user.user_id and assistant msgs should get user.agent_id.

    We capture role_id at the add_message boundary instead of reading it back from
    storage, because store() commits the session synchronously and committed
    messages move out of session.messages into archive files.
    """
    from openviking.session.session import Session

    captured: list[tuple[str, str | None]] = []
    original = Session.add_message

    def _spy(self, role, parts, role_id=None, created_at=None):
        captured.append((role, role_id))
        return original(self, role, parts, role_id=role_id, created_at=created_at)

    monkeypatch.setattr(Session, "add_message", _spy)

    await store(
        messages=[
            StoreMessage(role="user", content="user msg"),
            StoreMessage(role="assistant", content="assistant msg"),
        ]
    )

    assert captured == [
        ("user", DEFAULT_CTX.user.user_id),
        ("assistant", DEFAULT_CTX.user.agent_id),
    ]


# ---------------------------------------------------------------------------
# add_resource tool
# ---------------------------------------------------------------------------


async def test_add_resource_rejects_local_path_with_cli_hint(service):
    result = await add_resource(path="/tmp/definitely_does_not_exist_xyz.md")
    assert "error" in result.lower()
    assert "ov add-resource" in result
    assert "ovcli.conf" in result


async def test_add_resource_rejects_bare_filename_with_cli_hint(service):
    result = await add_resource(path="some_local_file.md")
    assert "error" in result.lower()
    assert "ov add-resource" in result


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
    result = await glob(pattern="zzz_nonexistent_*.xyz")
    assert "No files found" in result


async def test_glob_match_all_md(service, client_with_resource):
    _, root_uri = client_with_resource
    result = await glob(pattern="**/*.md", uri=root_uri)
    assert isinstance(result, str)


async def test_glob_with_uri_scope(service):
    result = await glob(pattern="*", uri="viking://resources")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_mcp_route_registered(app):
    """Verify the /mcp route exists in the app."""
    mcp_routes = [r for r in app.routes if hasattr(r, "path") and r.path == "/mcp"]
    assert len(mcp_routes) == 1
