# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for MCP endpoint tools (openviking/server/mcp_endpoint.py).

Tests the tool functions directly by setting up the identity contextvar
and service dependency, avoiding MCP protocol complexity.
"""

import base64
import json
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from jsonschema import Draft202012Validator
from mcp.types import AudioContent, CallToolResult, ImageContent, TextContent, Tool, ToolAnnotations
from starlette.routing import Route

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.privacy.reference import build_privacy_field_reference
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.dependencies import set_service
from openviking.server.identity import AuthMode, RequestContext, Role
from openviking.server.mcp_endpoint import (
    StoreMessage,
    _get_ctx,
    _IdentityASGIMiddleware,
    _mcp_ctx,
    _resolve_mcp_workspace_uri,
    add_resource,
    add_skill,
    cancel_watch,
    download,
    edit,
    forget,
    glob,
    grep,
    health,
    list_watches,
    read,
    remember,
    rule_search,
    search,
    tree,
    write,
)
from openviking.server.mcp_endpoint import ls as list_tool
from openviking.server.routers import content as content_router
from openviking_cli.exceptions import (
    InvalidArgumentError,
    InvalidURIError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
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


def test_get_ctx_raises_when_unset():
    token = _mcp_ctx.set(None)
    try:
        with pytest.raises(UnauthenticatedError, match="MCP request identity not set"):
            _get_ctx()
    finally:
        _mcp_ctx.reset(token)


async def test_privacy_tools_describe_placeholder_resolution_and_permissions():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    for name in ("privacy_tree", "privacy_resolve"):
        assert tools[name].annotations.readOnlyHint is True
    assert tools["privacy_upsert"].annotations.readOnlyHint is False
    assert tools["privacy_upsert"].annotations.destructiveHint is True
    resolve_tool = tools["privacy_resolve"]
    assert (
        "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}"
        in resolve_tool.description
    )
    assert "SKILL.md" in resolve_tool.description
    assert "Authorization" in resolve_tool.description
    assert set(resolve_tool.inputSchema["properties"]) == {"reference"}
    assert resolve_tool.inputSchema["required"] == ["reference"]
    assert set(tools["privacy_upsert"].inputSchema["required"]) == {
        "category",
        "target_key",
        "values",
    }
    assert tools["privacy_upsert"].inputSchema["properties"]["values"]["type"] == "object"


async def test_privacy_tools_merge_discover_and_resolve_without_echoing_saved_values(client):
    secrets = ["pat-original-marker", "pat-updated-marker", "username-private-marker"]
    saved = await mcp_endpoint.mcp.call_tool(
        "privacy_upsert",
        {
            "category": "credential",
            "target_key": "github",
            "values": {"pat": secrets[0], "username": secrets[2]},
        },
    )
    assert saved[1]["version"] == 1
    update = {"category": "credential", "target_key": "github", "values": {"pat": secrets[1]}}
    patched = await mcp_endpoint.mcp.call_tool("privacy_upsert", update)
    assert patched[1]["version"] == 2
    assert [field["field"] for field in patched[1]["fields"]] == ["pat"]
    repeated = await mcp_endpoint.mcp.call_tool("privacy_upsert", update)
    assert repeated[1]["version"] == 2
    tree = await mcp_endpoint.mcp.call_tool("privacy_tree", {"category": "credential"})
    response = await client.get(
        "/api/v1/privacy-configs/tree",
        params={"category": "credential"},
        headers={"X-OpenViking-User": DEFAULT_CTX.user.user_id},
    )
    assert response.status_code == 200, response.text
    assert tree[1] == response.json()["result"]
    for result in (saved, patched, repeated, tree):
        assert all(secret not in repr(result) for secret in secrets)
        assert '"values"' not in json.dumps(result[1])
    target = tree[1]["categories"][0]["targets"][0]
    assert target["version"] == 2
    resolved = await mcp_endpoint.mcp.call_tool(
        "privacy_resolve",
        {
            "reference": target["fields"][0]["reference"],
        },
    )
    assert resolved[1]["value"] == secrets[1]
    assert resolved[1]["version"] == 2
    assert "username" not in resolved[1]
    retained = await mcp_endpoint.mcp.call_tool(
        "privacy_resolve",
        {
            "reference": "viking://~/privacy/credential/github#/values/username",
        },
    )
    assert retained[1]["value"] == secrets[2]


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
async def test_privacy_resolve_checks_owner_before_storage(service, monkeypatch, role):
    exists = AsyncMock()
    monkeypatch.setattr(service.privacy_configs, "exists", exists)
    token = _mcp_ctx.set(RequestContext(DEFAULT_CTX.user, role))
    try:
        result = await mcp_endpoint.mcp.call_tool(
            "privacy_resolve",
            {
                "reference": build_privacy_field_reference(
                    "another-owner", "credential", "github", "pat"
                ),
            },
        )
    finally:
        _mcp_ctx.reset(token)
    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "PERMISSION_DENIED"
    exists.assert_not_awaited()


async def test_privacy_tools_keep_same_owner_in_other_accounts_isolated():
    await mcp_endpoint.mcp.call_tool(
        "privacy_upsert",
        {
            "category": "credential",
            "target_key": "github",
            "values": {"pat": "account-private-marker"},
        },
    )
    token = _mcp_ctx.set(
        RequestContext(UserIdentifier("another-account", DEFAULT_CTX.user.user_id), Role.USER)
    )
    try:
        tree = await mcp_endpoint.mcp.call_tool("privacy_tree", {})
        missing = await mcp_endpoint.mcp.call_tool(
            "privacy_resolve",
            {
                "reference": build_privacy_field_reference(
                    DEFAULT_CTX.user.user_id, "credential", "github", "pat"
                ),
            },
        )
    finally:
        _mcp_ctx.reset(token)
    assert tree[1]["categories"] == []
    assert missing.isError is True
    assert missing.structuredContent["error"]["code"] == "NOT_FOUND"
    assert "account-private-marker" not in repr((tree, missing))


async def test_privacy_tools_return_structured_errors_without_values():
    await mcp_endpoint.mcp.call_tool(
        "privacy_upsert",
        {
            "category": "credential",
            "target_key": "github",
            "values": {"pat": "private-input-marker"},
        },
    )
    cases = [
        ("privacy_tree", {"category": "../credential"}, "INVALID_ARGUMENT"),
        (
            "privacy_resolve",
            {"reference": "Authorization: Bearer {{ov_privacy:...}}"},
            "INVALID_ARGUMENT",
        ),
        (
            "privacy_resolve",
            {"reference": "viking://~/privacy/credential/github#/values/missing"},
            "NOT_FOUND",
        ),
        (
            "privacy_upsert",
            {
                "category": "credential",
                "target_key": "bad/target",
                "values": {"pat": "private-input-marker"},
            },
            "INVALID_ARGUMENT",
        ),
    ]
    for name, arguments, code in cases:
        result = await mcp_endpoint.mcp.call_tool(name, arguments)
        assert isinstance(result, CallToolResult)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == code
        assert "private-input-marker" not in repr(result)


async def test_privacy_tools_handle_disabled_service(service, monkeypatch):
    monkeypatch.setattr(service, "_privacy_config_service", None)
    tree = await mcp_endpoint.mcp.call_tool("privacy_tree", {})
    assert tree[1] == {"owner_user_id": DEFAULT_CTX.user.user_id, "categories": []}
    for name, arguments, code in [
        (
            "privacy_resolve",
            {"reference": "viking://~/privacy/credential/github#/values/pat"},
            "NOT_FOUND",
        ),
        (
            "privacy_upsert",
            {
                "category": "credential",
                "target_key": "github",
                "values": {"pat": "disabled-private-marker"},
            },
            "FAILED_PRECONDITION",
        ),
    ]:
        result = await mcp_endpoint.mcp.call_tool(name, arguments)
        assert result.isError is True
        assert result.structuredContent["error"]["code"] == code
        assert "disabled-private-marker" not in repr(result)


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("viking://user", "viking://user"),
        ("viking://user/notes.md", "viking://user/notes.md"),
        (
            "viking://user/project/notes.md",
            "viking://user/project/notes.md",
        ),
        (
            "viking://user/test_user/project/notes.md",
            "viking://user/test_user/project/notes.md",
        ),
        ("viking://resources/project/notes.md", "viking://resources/project/notes.md"),
        ("viking://~/resources", "viking://user/test_user/resources"),
    ],
)
@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
def test_resolve_mcp_workspace_uri_only_expands_documented_aliases(uri, expected, role):
    ctx = RequestContext(DEFAULT_CTX.user, role)
    assert _resolve_mcp_workspace_uri(uri, ctx) == expected


@pytest.mark.parametrize(
    "segment", ["memories", "resources", "skills", "peers", "privacy", "sessions"]
)
def test_resolve_mcp_workspace_uri_rejects_reserved_user_root_shorthand(segment):
    user_ctx = RequestContext(DEFAULT_CTX.user, Role.USER)
    with pytest.raises(InvalidURIError, match=re.escape(f"viking://~/{segment}")):
        _resolve_mcp_workspace_uri(f"viking://user/{segment}", user_ctx)


def test_resolve_mcp_workspace_uri_supports_dotted_current_user_id():
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("alice.smith@corp.com"),
        role=Role.USER,
    )

    assert (
        _resolve_mcp_workspace_uri("viking://user/alice.smith@corp.com/notes/todo.md", ctx)
        == "viking://user/alice.smith@corp.com/notes/todo.md"
    )
    assert _resolve_mcp_workspace_uri("viking://user/notes/todo.md", ctx) == (
        "viking://user/notes/todo.md"
    )
    # DEFAULT_CTX is ROOT: only the '~' alias uses its effective user identity,
    # so a reserved first segment stays a literal user id.
    assert _resolve_mcp_workspace_uri("viking://user/resources", DEFAULT_CTX) == (
        "viking://user/resources"
    )


# ---------------------------------------------------------------------------
# health tool
# ---------------------------------------------------------------------------


async def test_health_returns_healthy(service):
    result = await health()
    assert result.startswith("OpenViking is healthy (service initialized")
    assert "VikingFS" in result


async def test_health_returns_unhealthy_when_no_service(monkeypatch):
    monkeypatch.setattr(
        "openviking.server.mcp_endpoint.get_service",
        lambda: (_ for _ in ()).throw(RuntimeError("not initialized")),
    )
    result = await health()
    assert result == "OpenViking is unhealthy: not initialized"


# ---------------------------------------------------------------------------
# search tool
# ---------------------------------------------------------------------------


async def test_rule_search_tool_schema_and_annotations():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    tool = tools["rule_search"]
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert set(tool.inputSchema["properties"]) == {
        "task_context",
        "rule_probability_threshold",
    }
    assert tool.inputSchema["required"] == ["task_context"]


async def test_rule_search_returns_rendered_rules(service, monkeypatch):
    search_rules = AsyncMock(
        return_value={"rendered": "# Applicable Rules\n\n- Confirm before deleting."}
    )
    monkeypatch.setattr(service.rules, "search_rules", search_rules)

    result = await rule_search(
        task_context="Delete a generated report after confirming it is obsolete.",
        rule_probability_threshold=0.7,
    )

    assert result == "# Applicable Rules\n\n- Confirm before deleting."
    request = search_rules.await_args.args[0]
    assert request.task_context == "Delete a generated report after confirming it is obsolete."
    assert request.rule_probability_threshold == 0.7
    assert search_rules.await_args.kwargs["ctx"] == DEFAULT_CTX


async def test_search_returns_scoped_hits_until_min_score_excludes_them(client_with_resource):
    _, root_uri = client_with_resource
    arguments: dict[str, Any] = {
        "query": "resource management semantic search",
        "context_type": "resource",
        "target_uri": root_uri,
        "level": [2],
        "limit": 1,
    }
    result = await search(**arguments)
    assert "Found 1 item(s)" in result
    assert f"[resource 100%] {root_uri}/sample.md" in result

    filtered = await search(**arguments, min_score=1.0)
    assert filtered == "No matching context found."


async def test_search_tools_expose_context_type_and_item_filter():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    for tool_name in ("find", "search"):
        properties = tools[tool_name].inputSchema["properties"]
        assert "context_type" in properties
        assert properties["context_type"]["description"] == (
            "Filter results to one or more context types: memory, resource, or skill."
        )
        assert properties["tags"]["type"] == "array"
        assert "include_tags" not in properties
        assert "filter" in properties
        assert "search_as_item" in properties


async def test_retrieval_tools_expose_tag_parameters_with_compatible_defaults():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    for tool_name in ("find", "search", "list", "tree", "grep"):
        properties = tools[tool_name].inputSchema["properties"]
        assert properties["tags"]["type"] == "array"
        assert properties["tags"]["description"] == (
            "Filter results by tags; every supplied tag must match and use k=v format "
            "(for example, team=search)."
        )
        assert "tags" not in tools[tool_name].inputSchema.get("required", [])

    for tool_name in ("list", "tree", "grep"):
        properties = tools[tool_name].inputSchema["properties"]
        assert properties["include_tags"]["type"] == "boolean"
        assert properties["include_tags"]["default"] is False
        assert properties["include_tags"]["description"] == (
            "Include each result's tags in the output; this does not filter results."
        )

    for tool_name in ("find", "search", "list", "tree", "grep"):
        assert "k=v" not in tools[tool_name].description
    for tool_name in ("list", "tree", "grep"):
        assert "include_tags" not in tools[tool_name].description


async def test_search_marks_every_context_only_parameter_in_its_description():
    from openviking.server.routers.search import CONTEXT_ONLY_FIELDS

    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    properties = tools["search"].inputSchema["properties"]
    context_only = {*CONTEXT_ONLY_FIELDS, *mcp_endpoint._MCP_CONTEXT_ONLY_ALIASES}

    # List mode rejects these, and the parameter description is the only place a
    # caller reading the schema can learn that before spending a failed call.
    unmarked = sorted(
        name
        for name in context_only
        if "mode='context'" not in properties[name].get("description", "")
    )
    assert unmarked == []


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
        if "items" in node:
            assert node["type"] == "array", f"{path}: items on a non-array schema"
        for key, sub in node.get("properties", {}).items():
            assert_portable(sub, f"{path}.{key}")
        for key in ("items", "additionalProperties"):
            if isinstance(node.get(key), dict):
                assert_portable(node[key], f"{path}.{key}")

    tools = await mcp_endpoint.mcp.list_tools()
    assert tools
    for tool in tools:
        assert_portable(tool.inputSchema, tool.name)

    schemas = {tool.name: tool.inputSchema for tool in tools}
    uri = "viking://resources/docs"
    for name, pattern in (("grep", ["TODO"]), ("glob", "**/*.md")):
        validator = Draft202012Validator(schemas[name])
        assert validator.is_valid({"uri": uri, "pattern": pattern})
        assert not validator.is_valid({"uri": [uri], "pattern": pattern})
    assert schemas["glob"]["properties"]["uri"]["default"] == "viking://"


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


async def test_find_tool_combines_context_type_and_tag_filters(service, monkeypatch):
    captured = {}
    target = "viking://resources/project"

    async def fake_find(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            memories=[],
            resources=[
                SimpleNamespace(
                    uri=f"{target}/guide.md",
                    abstract="Guide",
                    score=0.8,
                    search_tags=["team=search", "env=prod"],
                )
            ],
            skills=[],
        )

    monkeypatch.setattr(service.search, "find", fake_find)

    result = await mcp_endpoint.find(
        query="guide",
        target_uri=target,
        limit=2,
        min_score=0.2,
        context_type=["memory", "resource"],
        tags=["Team=Search", "env=prod"],
    )

    assert captured["query"] == "guide"
    assert captured["ctx"] == DEFAULT_CTX
    assert captured["target_uri"] == target
    assert captured["limit"] == 2
    assert captured["score_threshold"] == 0.2
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "context_type", "conds": ["memory", "resource"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }
    assert f'{target}/guide.md tags=["team=search", "env=prod"]' in result


async def test_find_tool_skill_only_searches_both_skill_roots(service):
    private_root = "viking://user/test_user/skills/review"
    shared_root = "viking://agent/skills/review"
    query = "review a pull request"
    vector = service.viking_fs.query_embedder.embed(query).dense_vector
    for root, label, tags in (
        (private_root, "Private review", ["team=search"]),
        (shared_root, "Shared review", ["team=core"]),
    ):
        await service.viking_fs.write_file(
            f"{root}/SKILL.md", f"# {label} instructions", ctx=DEFAULT_CTX
        )
        if root == shared_root:
            await service.viking_fs.write_file(
                f"{root}/.abstract.md", "Shared package summary", ctx=DEFAULT_CTX
            )
        for name in ("check.md", "other.md"):
            uri = f"{root}/scripts/{name}"
            await service.viking_fs.write_file(uri, "# Implement the review", ctx=DEFAULT_CTX)
            await service.vikingdb_manager.upsert(
                {
                    "uri": uri,
                    "parent_uri": f"{root}/scripts",
                    "context_type": "skill",
                    "abstract": f"{label} script",
                    "search_tags": tags,
                    "vector": vector,
                    "level": 2,
                    "is_leaf": True,
                },
                ctx=DEFAULT_CTX,
            )

    ctx = RequestContext(DEFAULT_CTX.user, Role.USER)
    token = _mcp_ctx.set(ctx)
    try:
        result = await mcp_endpoint.find(
            query=query, context_type="skill", read_content=True, level=[2]
        )
        assert "Found 2 item(s)" in result
        for root, label in ((private_root, "Private review"), (shared_root, "Shared review")):
            assert result.count(f"{root}/SKILL.md") == 1
            assert f"# {label} instructions" in result
        assert "Shared package summary" in result
        assert "Shared review script" not in result
        assert "Private review script" in result
        assert "scripts/" not in result and "is not ready" not in result

        selected = await mcp_endpoint.find(query=query, context_type=["skill"], tags=["Team=Core"])
        assert "Found 1 item(s)" in selected
        assert f'{shared_root}/SKILL.md tags=["team=core"]' in selected
        assert private_root not in selected

        scoped = await mcp_endpoint.find(
            query=query, context_type="skill", target_uri="viking://~/skills"
        )
        assert f"{private_root}/SKILL.md" in scoped and shared_root not in scoped

        filter_only = await mcp_endpoint.find(
            query="", context_type="skill", level=[2], tags=["team=core"]
        )
        assert f"{shared_root}/SKILL.md" in filter_only and private_root not in filter_only
    finally:
        _mcp_ctx.reset(token)


async def test_find_tool_collapses_several_hits_from_one_skill_package(service, monkeypatch):
    hits = [
        SimpleNamespace(
            uri="viking://agent/skills/deploy/scripts/rollback.sh",
            abstract="Roll back the last release",
            score=0.44,
        ),
        SimpleNamespace(
            uri="viking://agent/skills/deploy/.overview.md",
            abstract="How the deploy skill works",
            score=0.71,
        ),
    ]

    async def fake_find(**kwargs):
        return SimpleNamespace(memories=[], resources=[], skills=hits)

    monkeypatch.setattr(service.search, "find", fake_find)

    result = await mcp_endpoint.find(query="roll back", context_type=["skill", "memory"])

    assert "Found 1 item(s)" in result
    assert "- [skill 71%] viking://agent/skills/deploy/SKILL.md" in result
    assert "tags=[]" in result
    assert "How the deploy skill works" in result


@pytest.mark.parametrize(
    "hit_uri",
    [
        "viking://user/test_user/skills/.review.update-backup-ab12/SKILL.md",
        "viking://agent/skills",
    ],
)
async def test_find_tool_leaves_a_uri_outside_a_skill_package_alone(service, monkeypatch, hit_uri):
    hit = SimpleNamespace(uri=hit_uri, abstract="Not an installed skill", score=0.4)

    async def fake_find(**kwargs):
        return SimpleNamespace(memories=[], resources=[], skills=[hit])

    monkeypatch.setattr(service.search, "find", fake_find)

    result = await mcp_endpoint.find(query="review", context_type=["skill", "memory"])

    assert f"- [skill 40%] {hit_uri} tags=[]\n" in result
    assert "SKILL.md/SKILL.md" not in result


async def test_find_tool_keeps_the_package_abstract_of_a_sidecar_hit(service, monkeypatch):
    hit = SimpleNamespace(
        uri="viking://agent/skills/pdf-forms/.abstract.md",
        abstract="name: pdf-forms\ndescription: Fill in a PDF form",
        score=0.52,
    )

    abstract_uris = []

    async def fake_find_skills(**kwargs):
        return SimpleNamespace(memories=[], resources=[], skills=[hit])

    async def fake_abstract(uri, ctx):
        abstract_uris.append(uri)
        return ""

    monkeypatch.setattr(service.search, "find_skills", fake_find_skills)
    monkeypatch.setattr(service.fs, "abstract", fake_abstract)

    result = await mcp_endpoint.find(query="fill a pdf", context_type="skill")

    # A package-level hit already carries the skill's abstract.
    assert abstract_uris == []
    assert "description: Fill in a PDF form" in result


async def test_search_tool_collapses_skill_package_hits(service, monkeypatch):
    hits = [
        SimpleNamespace(
            uri="viking://agent/skills/deploy/scripts/rollback.sh",
            abstract="Roll back the last release",
            score=0.44,
        ),
        SimpleNamespace(
            uri="viking://agent/skills/deploy/.overview.md",
            abstract="How the deploy skill works",
            score=0.71,
        ),
    ]

    async def fake_search(**kwargs):
        return SimpleNamespace(memories=[], resources=[], skills=hits)

    monkeypatch.setattr(service.search, "search", fake_search)

    result = await search(query="roll back", context_type="skill")

    assert "Found 1 item(s)" in result
    assert "- [skill 71%] viking://agent/skills/deploy/SKILL.md" in result


async def test_find_tool_inlines_visible_content_when_requested(service, monkeypatch):
    async def fake_find(**kwargs):
        del kwargs
        return SimpleNamespace(
            memories=[],
            resources=[
                SimpleNamespace(
                    uri="viking://resources/visible.md",
                    abstract="summary",
                    overview="",
                    score=0.9,
                )
            ],
            skills=[],
        )

    async def fake_read_visible(uri, *, ctx):
        assert uri == "viking://resources/visible.md"
        assert ctx == DEFAULT_CTX
        return "full visible content"

    monkeypatch.setattr(service.search, "find", fake_find)
    monkeypatch.setattr(service.fs, "read_visible", fake_read_visible)

    result = await mcp_endpoint.find(query="visible", read_content=True)

    assert "full visible content" in result
    assert "Use the read tool" not in result


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
        target_uri="viking://user/test_user/project",
        session_id="session-1",
        limit=4,
        min_score=0.1,
        context_type="skill",
        tags=["team=search"],
    )

    assert result == "No matching context found."
    assert session.load_called is True
    assert captured["session_factory_ctx"] == DEFAULT_CTX
    assert captured["session_id"] == "session-1"
    assert captured["query"] == "deep lookup"
    assert captured["ctx"] == DEFAULT_CTX
    assert captured["target_uri"] == "viking://user/test_user/project"
    assert captured["session"] == session
    assert captured["limit"] == 4
    assert captured["score_threshold"] == 0.1
    assert captured["filter"] == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "context_type", "conds": ["skill"]},
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
        ],
    }


async def test_search_context_mode_returns_assembled_context(service, monkeypatch):
    captured = {}

    async def fake_assemble_context(*, service, ctx, params):
        captured.update(service=service, ctx=ctx, params=params)
        return SimpleNamespace(
            digest="",
            rendered='<memory uri="viking://user/test_user/memories/events/e.md">event</memory>',
        )

    monkeypatch.setattr(mcp_endpoint, "assemble_context", fake_assemble_context)

    result = await search(
        query="what happened",
        mode="context",
        events_time_decay_protection="2d",
        quotas={"events": 1, "entities": 0},
        purpose="coding",
        min_score=0.1,
        max_tokens=800,
        detail_by_category={"events": "overview"},
        dedup_turns=5,
        exclude_uris=["viking://user/test_user/memories/events/old.md"],
        peer_scope="actor",
        other_peer_penalties={"events": 0.2},
        tags=["team=search", "env=prod"],
        rewrite="auto",
        rewrite_max_bullets=4,
    )

    assert result.startswith("<memory")
    assert captured["service"] is service
    assert captured["ctx"] == DEFAULT_CTX
    params = captured["params"]
    assert params.query == "what happened"
    assert params.quotas == {"events": 1, "entities": 0}
    assert params.purpose == "coding"
    assert params.score_threshold == 0.1
    assert params.events_time_decay_protection == "2d"
    assert params.max_tokens == 800
    assert params.detail == {"events": "overview"}
    assert params.dedup_turns == 5
    assert params.exclude_uris == ["viking://user/test_user/memories/events/old.md"]
    assert params.peer_scope == "actor"
    assert params.other_peer_penalty == {"events": 0.2}
    assert params.filter == {
        "op": "and",
        "conds": [
            {"op": "must", "field": "search_tags", "conds": ["team=search"]},
            {"op": "must", "field": "search_tags", "conds": ["env=prod"]},
        ],
    }
    assert params.rewrite == "auto"
    assert params.rewrite_max_bullets == 4


async def test_search_mode_defaults_preserve_list_threshold_but_not_context_threshold(
    service, monkeypatch
):
    captured = {}

    async def fake_search(**kwargs):
        captured["list_threshold"] = kwargs["score_threshold"]
        return SimpleNamespace(memories=[], resources=[], skills=[])

    async def fake_assemble_context(*, service, ctx, params):
        captured["context_threshold"] = params.score_threshold
        return SimpleNamespace(digest="", rendered="")

    monkeypatch.setattr(service.search, "search", fake_search)
    monkeypatch.setattr(mcp_endpoint, "assemble_context", fake_assemble_context)

    await search(query="list default")
    await search(query="context default", mode="context")

    assert captured == {"list_threshold": 0.35, "context_threshold": None}


async def test_retrieval_tools_describe_advanced_parameters_without_changing_contracts():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    assert "recall" not in tools
    shared = {"level": "array", "context_type": "array", "events_time_decay_protection": "string"}
    context = {
        "quotas": "object",
        "detail": "string",
        "detail_by_category": "object",
        "dedup_turns": "integer",
        "peer_scope": "string",
        "other_peer_penalty": "number",
        "other_peer_penalties": "object",
        "rewrite": "string",
    }
    for name, types, defaults in (
        ("find", shared, {}),
        (
            "search",
            shared | context,
            {"detail": "auto", "dedup_turns": 0, "peer_scope": "all", "rewrite": "off"},
        ),
    ):
        schema = tools[name].inputSchema
        fields = schema["properties"]
        assert set(types).isdisjoint(schema.get("required", []))
        assert {key: fields[key]["type"] for key in types} == types
        assert {
            key: fields[key]["default"] for key in types if "default" in fields[key]
        } == defaults
        assert "0=abstract, 1=overview, 2=detail/content" in fields["level"]["description"]
        decay = fields["events_time_decay_protection"]["description"]
        assert "updated_at" in decay and "non-empty query" in decay

    search = tools["search"].inputSchema["properties"]
    assert search["mode"]["enum"] == ["list", "context"]
    assert search["detail"]["enum"] == ["auto", "abstract", "overview", "full"]
    assert search["rewrite"]["enum"] == ["off", "auto"]
    for field, guidance in {
        "quotas": "instead of limit",
        "level": "ignored in mode='context'",
        "detail_by_category": "non-auto detail",
        "dedup_turns": "0 disables cross-turn deduplication",
        "peer_scope": "other peers",
        "other_peer_penalty": "cannot be combined with other_peer_penalties",
        "other_peer_penalties": "omitted categories keep their defaults",
        "rewrite": "on failure, the assembled context is returned unchanged",
    }.items():
        assert guidance in search[field]["description"]


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
    result = await read("viking://user/test_user/memories/does_not_exist.md")
    assert "not found" in result.lower()


async def test_read_directory_uri_returns_recoverable_hint(service):
    uri = "viking://resources/test_read_dir_hint"
    await service.viking_fs.mkdir(uri, ctx=DEFAULT_CTX, exist_ok=True)

    result = await read(uri)

    assert "Directory URI is not readable as a file" in result
    assert "List it first, then read a file URI." in result
    assert uri in result
    assert "nothing found" not in result.lower()


async def test_read_batch(service):
    visible = "viking://resources/read-batch/visible.md"
    missing = "viking://resources/read-batch/missing.md"
    arguments = {"uris": [missing, visible]}
    failed = await mcp_endpoint.mcp.call_tool("read", arguments)
    assert isinstance(failed, CallToolResult) and failed.isError
    assert failed.structuredContent is None
    assert f"=== {missing} ===\nNOT_FOUND:" in failed.content[0].text
    assert f"=== {visible} ===\nNOT_FOUND:" in failed.content[0].text

    await service.viking_fs.write_file(visible, "Visible batch content", ctx=DEFAULT_CTX)
    partial = await mcp_endpoint.mcp.call_tool("read", arguments)
    assert isinstance(partial, list) and len(partial) == 1
    assert isinstance(partial[0], TextContent)
    assert partial[0].text.startswith(f"=== {missing} ===\nNOT_FOUND:")
    assert partial[0].text.endswith(f"=== {visible} ===\nVisible batch content")
    assert await mcp_endpoint.mcp.call_tool("read", {"uri": arguments["uris"]}) == partial
    for key in ("uri", "uris"):
        single = await mcp_endpoint.mcp.call_tool("read", {key: visible})
        assert single == [TextContent(type="text", text="Visible batch content")]


async def test_read_passes_offset_limit_to_visible_read(service):
    uri = "viking://user/test_user/memories/notes/private.md"
    visible = "line one\nline two\nline three\nline four"
    await service.viking_fs.write_file(
        uri,
        visible + '\n\n<!-- MEMORY_FIELDS\n{"internal": "stored metadata"}\n-->',
        ctx=DEFAULT_CTX,
    )
    assert await read(uri) == visible

    result = await mcp_endpoint.mcp.call_tool(
        "read",
        {
            "uris": uri,
            "offset": 1,
            "limit": 2,
        },
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], TextContent)
    assert result[0].text == "line two\nline three\n"


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"uri": "viking://resources/a.md", "uris": "viking://resources/b.md"}, "not both"),
        ({}, "read takes uri for one file"),
        ({"uris": []}, "read takes uri for one file"),
        ({"uri": ""}, "read takes uri for one file"),
    ],
)
async def test_read_rejects_ambiguous_or_missing_uri(arguments, message):
    result = await mcp_endpoint.mcp.call_tool("read", arguments)

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text.startswith("INVALID_ARGUMENT: ")
    assert message in result.content[0].text


def test_read_schema_offers_uri_and_uris():
    schema = mcp_endpoint.mcp._tool_manager.get_tool("read").parameters
    assert schema["properties"]["uri"]["type"] == "string"
    assert "items" not in schema["properties"]["uri"]
    assert schema["properties"]["uris"]["type"] == "array"
    assert schema["properties"]["uris"]["items"]["type"] == "string"
    assert schema.get("required", []) == []


@pytest.mark.parametrize(
    ("uri", "image_bytes", "mime_type"),
    [
        ("viking://resources/result.png", b"\x89PNG\r\n\x1a\nimage", "image/png"),
        ("viking://resources/result.jpg", b"\xff\xd8\xffimage", "image/jpeg"),
        ("viking://resources/result.gif", b"GIF89aimage", "image/gif"),
        ("viking://resources/result.webp", b"RIFF\x00\x00\x00\x00WEBPimage", "image/webp"),
    ],
)
async def test_read_image_returns_native_mcp_content(monkeypatch, uri, image_bytes, mime_type):
    read_file_bytes = AsyncMock(return_value=image_bytes)
    read_visible = AsyncMock()
    stat = AsyncMock(return_value={"size": len(image_bytes), "isDir": False})
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=read_visible,
                stat=stat,
            ),
            mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
        ),
    )
    result = await mcp_endpoint.mcp.call_tool("read", {"uris": uri})

    assert isinstance(result, list)
    assert isinstance(result[0], TextContent)
    assert result[0].text == f"Source: {uri}"
    assert isinstance(result[1], ImageContent)
    assert result[1].mimeType == mime_type
    assert base64.b64decode(result[1].data) == image_bytes
    read_file_bytes.assert_awaited_once_with(uri, ctx=DEFAULT_CTX)
    read_visible.assert_not_awaited()


async def test_read_mixed_batch_preserves_source_order(monkeypatch):
    image_bytes = b"\xff\xd8\xffimage"
    read_visible = AsyncMock(return_value="notes")
    read_file_bytes = AsyncMock(return_value=image_bytes)
    stat = AsyncMock(return_value={"size": len(image_bytes), "isDir": False})
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=read_visible,
                stat=stat,
            ),
            mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
        ),
    )
    text_uri = "viking://resources/notes.md"
    image_uri = "viking://resources/chart.jpg"

    result = await mcp_endpoint.mcp.call_tool("read", {"uris": [text_uri, image_uri]})

    assert isinstance(result, list)
    assert [block.type for block in result] == ["text", "text", "text", "image"]
    assert result[0].text == f"=== {text_uri} ==="
    assert result[1].text == "notes"
    assert result[2].text == f"=== {image_uri} ==="
    assert result[3].mimeType == "image/jpeg"


@pytest.mark.parametrize(
    ("uri", "audio_bytes", "mime_type"),
    [
        ("viking://resources/clip.wav", b"RIFF\x00\x00\x00\x00WAVEaudio", "audio/wav"),
        ("viking://resources/clip.mp3", b"ID3audio", "audio/mpeg"),
        ("viking://resources/clip.flac", b"fLaCaudio", "audio/flac"),
        ("viking://resources/clip.ogg", b"OggSaudio", "audio/ogg"),
        ("viking://resources/clip.m4a", b"\x00\x00\x00\x18ftypM4A audio", "audio/mp4"),
        # suffix sniffing must ignore a query string, like the extension gate does
        ("viking://resources/clip.ogg?v=2", b"OggSaudio", "audio/ogg"),
    ],
)
async def test_read_audio_returns_native_mcp_content(monkeypatch, uri, audio_bytes, mime_type):
    read_file_bytes = AsyncMock(return_value=audio_bytes)
    stat = AsyncMock(return_value={"size": len(audio_bytes), "isDir": False})
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=AsyncMock(),
                stat=stat,
            )
        ),
    )

    result = await mcp_endpoint.mcp.call_tool("read", {"uris": uri})

    assert isinstance(result, list)
    assert isinstance(result[0], TextContent)
    assert result[0].text == f"Source: {uri}"
    assert isinstance(result[1], AudioContent)
    assert result[1].mimeType == mime_type
    assert base64.b64decode(result[1].data) == audio_bytes


async def test_read_video_returns_unsupported_hint(monkeypatch):
    stat = AsyncMock(return_value={"size": 1024, "isDir": False})
    read_visible = AsyncMock()
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=AsyncMock(),
                read_visible=read_visible,
                stat=stat,
            )
        ),
    )

    result = await read("viking://resources/demo.mp4")

    assert "no standard VideoContent" in result
    assert 'ov get "viking://resources/demo.mp4" "./demo.mp4"' in result
    stat.assert_awaited_once_with(
        "viking://resources/demo.mp4",
        ctx=DEFAULT_CTX,
        skip_count=True,
    )
    read_visible.assert_not_awaited()


async def test_read_video_nonexistent_uri_preserves_not_found(monkeypatch):
    stat = AsyncMock(side_effect=NotFoundError("viking://resources/missing.mp4", "file"))
    read_visible = AsyncMock()
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=AsyncMock(),
                read_visible=read_visible,
                stat=stat,
            )
        ),
    )

    result = await read("viking://resources/missing.mp4")

    assert "not found" in result.lower()
    assert "VideoContent" not in result
    stat.assert_awaited_once_with(
        "viking://resources/missing.mp4",
        ctx=DEFAULT_CTX,
        skip_count=True,
    )
    read_visible.assert_not_awaited()


async def test_read_video_directory_uri_preserves_directory_hint(monkeypatch):
    stat = AsyncMock(return_value={"size": 0, "isDir": True})
    read_visible = AsyncMock()
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=AsyncMock(),
                read_visible=read_visible,
                stat=stat,
            )
        ),
    )

    result = await read("viking://resources/archive.mp4")

    assert "URI points to a directory" in result
    assert "VideoContent" not in result
    stat.assert_awaited_once_with(
        "viking://resources/archive.mp4",
        ctx=DEFAULT_CTX,
        skip_count=True,
    )
    read_visible.assert_not_awaited()


async def test_read_rejects_spoofed_image_extension(monkeypatch):
    read_file_bytes = AsyncMock(return_value=b"not an image")
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=AsyncMock(),
                stat=AsyncMock(return_value={"size": 12, "isDir": False}),
            )
        ),
    )

    result = await read("viking://resources/not-really.png")

    assert "bytes do not match" in result


async def test_read_rejects_images_too_large_for_common_clients(monkeypatch):
    read_file_bytes = AsyncMock()
    oversized = mcp_endpoint._MCP_MEDIA_MAX_BYTES + 1
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=AsyncMock(),
                stat=AsyncMock(return_value={"size": oversized, "isDir": False}),
            )
        ),
    )

    result = await read("viking://resources/huge.png")

    assert "too large to inline" in result
    assert 'ov get "viking://resources/huge.png" "./huge.png"' in result
    assert 'Call the download tool with uri="viking://resources/huge.png"' in result
    read_file_bytes.assert_not_awaited()


async def test_read_rejects_media_batch_over_aggregate_limit_before_read(monkeypatch):
    first_uri = "viking://resources/first.png"
    second_uri = "viking://resources/second.png"
    declared_size = mcp_endpoint._MCP_MEDIA_MAX_BYTES // 2 + 1
    read_file_bytes = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    stat = AsyncMock(return_value={"size": declared_size, "isDir": False})
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=AsyncMock(),
                stat=stat,
            )
        ),
    )

    result = await mcp_endpoint.mcp.call_tool("read", {"uris": [first_uri, second_uri]})

    assert isinstance(result, list)
    assert "combined media size" in result[3].text
    assert f'ov get "{second_uri}" "./second.png"' in result[3].text
    read_file_bytes.assert_awaited_once_with(first_uri, ctx=DEFAULT_CTX)


async def test_read_svg_remains_text(monkeypatch):
    read_visible = AsyncMock(return_value="<svg></svg>")
    read_file_bytes = AsyncMock()
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(
                read_file_bytes=read_file_bytes,
                read_visible=read_visible,
            ),
            mcps=SimpleNamespace(read_live_context=AsyncMock(return_value=None)),
        ),
    )
    uri = "viking://resources/diagram.svg"

    assert await read(uri) == "<svg></svg>"
    read_visible.assert_awaited_once_with(uri, ctx=DEFAULT_CTX, offset=0, limit=-1)
    read_file_bytes.assert_not_awaited()


async def test_read_tool_has_no_structured_output_schema():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    assert tools["read"].outputSchema is None


# ---------------------------------------------------------------------------
# list tool
# ---------------------------------------------------------------------------


async def test_list_root(service):
    result = await list_tool("viking://user")
    assert f"[dir] {DEFAULT_CTX.user.user_id}" in result.splitlines()


async def test_list_defaults_to_viking_root(service):
    result = await list_tool()
    assert result == await list_tool("viking://")
    assert {"[dir] resources", "[dir] user"}.issubset(result.splitlines())


async def test_list_empty_dir(service):
    uri = "viking://user/test_user/memories/empty_test"
    await service.viking_fs.mkdir(uri, ctx=DEFAULT_CTX, exist_ok=True)
    assert await list_tool(uri) == f"(no entries under {uri})"


async def test_list_reports_more_entries(service):
    await write(uri="viking://resources/test_list_limit/f1.md", content="1\n")
    await write(uri="viking://resources/test_list_limit/f2.md", content="2\n")

    result = await list_tool("viking://resources/test_list_limit", limit=1)

    assert "(more entries available;" in result


async def test_list_forwards_and_renders_tags(service):
    root = "viking://resources/tagged-listing"
    nested = f"{root}/topic"
    tagged = f"{nested}/guide.md"
    tagged_sibling = f"{nested}/reference.md"
    for uri, content in (
        (tagged, "Guide content."),
        (tagged_sibling, "Reference text."),
        (f"{nested}/other.md", "Other content."),
    ):
        await write(uri=uri, content=content, wait=True)
    for uri in (tagged, tagged_sibling):
        update = await service.fs.set_tags(
            uri, ["team=search"], mode="replace", recursive=False, ctx=DEFAULT_CTX
        )
        assert update["updated_uris"] == [uri]

    listing = await list_tool(nested, include_tags=True)
    expected_files = {
        '[file] guide.md tags=["team=search"]',
        '[file] reference.md tags=["team=search"]',
    }
    assert set(listing.splitlines()) == expected_files | {"[file] other.md tags=[]"}
    filtered_listing = await list_tool(nested, tags=["Team=Search"], include_tags=True)
    assert set(filtered_listing.splitlines()) == expected_files
    filtered = await tree(uri=root, tags=["Team=Search"], include_tags=True)
    assert set(filtered.splitlines()[1:]) == {
        'topic/guide.md (14 B) tags=["team=search"]',
        'topic/reference.md (15 B) tags=["team=search"]',
    }

    options = {"ctx": DEFAULT_CTX, "tags": ["team=search"], "node_limit": 0}
    entries = await service.fs.ls(nested, **options)
    simple = await service.fs.ls(nested, simple=True, **options)
    subtree = await service.fs.tree(root, **options)
    matches = await service.fs.glob("**/*.md", uri=root, **options)
    expected = {tagged, tagged_sibling}
    assert set(simple.entries) == expected and not simple.has_more
    for page in (entries, subtree):
        assert {item["uri"] for item in page.entries} == expected and not page.has_more
    assert matches["count"] == 2
    assert {entry["uri"] for entry in matches["matches"]} == expected

    await service.fs.set_tags(
        nested, ["team=search"], mode="replace", recursive=False, ctx=DEFAULT_CTX
    )
    directories_and_files = await tree(uri=root, tags=["Team=Search"], include_tags=True)
    lines = set(directories_and_files.splitlines()[1:])
    assert lines == set(filtered.splitlines()[1:]) | {'topic/ tags=["team=search"]'}


@pytest.mark.parametrize(
    ("tool_name", "arguments", "fs_method"),
    [
        ("list", {"uri": "viking://resources"}, "ls"),
        ("tree", {"uri": "viking://resources"}, "tree"),
        ("grep", {"uri": "viking://resources", "pattern": "text"}, "grep"),
    ],
)
async def test_filesystem_tools_reject_a_malformed_tag_filter(
    service, monkeypatch, tool_name, arguments, fs_method
):
    # The service drops a malformed tag, and a filter left empty returns every entry.
    fs_call = AsyncMock()
    monkeypatch.setattr(service.fs, fs_method, fs_call)

    result = await mcp_endpoint.mcp.call_tool(tool_name, {**arguments, "tags": ["team:search"]})

    assert isinstance(result, CallToolResult)
    assert result.isError is True
    assert result.content[0].text == (
        "INVALID_ARGUMENT: invalid search tag 'team:search': expected strict k=v format"
    )
    fs_call.assert_not_awaited()


# ---------------------------------------------------------------------------
# store tool
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("submitted", "archived"),
    [
        pytest.param(
            [("user", "The sky is blue")],
            [("user", "The sky is blue")],
            id="single",
        ),
        pytest.param(
            [("user", "My favorite color is blue"), ("user", ""), ("assistant", "Noted.")],
            [("user", "My favorite color is blue"), ("assistant", "Noted.")],
            id="batch-with-empty-message",
        ),
    ],
)
async def test_store_batch_messages(service, submitted, archived):
    ctx = RequestContext(DEFAULT_CTX.user, Role.USER, actor_peer_id="peer-a")
    token = _mcp_ctx.set(ctx)
    try:
        result = await remember(
            messages=[StoreMessage(role=role, content=content) for role, content in submitted]
        )
    finally:
        _mcp_ctx.reset(token)

    assert f"Submitted {len(archived)} message(s) for memory extraction" in result
    assert "Extraction runs in the background" in result
    assert "decides which memories to create or update" in result
    accepted = re.search(r"session (mcp-store-[0-9a-f]+), task_id=([^)]+)\)", result)
    assert accepted is not None
    session_id, task_id = accepted.groups()
    task = await service.sessions.get_commit_task(task_id, ctx)
    assert task is not None
    assert (task["task_type"], task["resource_id"]) == ("session_commit", session_id)
    session = await service.sessions.open(session_id, ctx)
    archive = await service.viking_fs.read_file(
        f"{session.uri}/history/archive_001/messages.jsonl", ctx=ctx
    )
    messages = [json.loads(line) for line in archive.splitlines()]
    assert [(message["role"], message["parts"][0]["text"]) for message in messages] == archived
    assert all(message.get("peer_id") is None for message in messages)


async def test_store_reports_skipped_commit(service):
    result = await remember(messages=[StoreMessage(role="user", content="")])
    assert result == "Nothing was committed for memory extraction (reason: no_messages)."


# ---------------------------------------------------------------------------
# add_skill tool
# ---------------------------------------------------------------------------


def _skill_md(name: str, description: str = "Review a PR diff before approving") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\nSteps.\n"


async def test_add_skill_inline_data_installs_into_user_skills(service):
    ctx = RequestContext(DEFAULT_CTX.user, Role.USER)
    token = _mcp_ctx.set(ctx)
    try:
        result = await add_skill(data=_skill_md("mcp-inline-skill"))
    finally:
        _mcp_ctx.reset(token)

    assert "Skill added: viking://user/test_user/skills/mcp-inline-skill" in result
    body = await service.fs.read(
        "viking://user/test_user/skills/mcp-inline-skill/SKILL.md", ctx=ctx
    )
    assert "# mcp-inline-skill" in body


async def test_add_skill_forwards_git_source_to_shared_installer(monkeypatch):
    captured = {}

    async def fake_install_skills(data, ctx, **kwargs):
        captured.update(kwargs, data=data)
        return {
            "skills": [{"name": "pdf", "description": "Fill PDF\nforms", "path": "pdf"}],
            "total": 1,
        }

    monkeypatch.setattr(mcp_endpoint, "install_skills", fake_install_skills)

    result = await add_skill(
        path="https://github.com/org/skills",
        skills=["pdf"],
        target_uri="viking://agent/skills",
        list_only=True,
    )

    assert captured["data"] == "https://github.com/org/skills"
    assert captured["names"] == ["pdf"]
    assert captured["list_only"] is True
    assert captured["target_uri"] == "viking://agent/skills"
    assert captured["source_metadata"] is None
    assert "nothing was installed" in result
    assert "- pdf (pdf): Fill PDF forms" in result


async def test_add_skill_reports_every_installed_skill(monkeypatch):
    async def fake_install_skills(data, ctx, **kwargs):
        return {
            "installed": [
                {"root_uri": "viking://user/test_user/skills/a"},
                {"root_uri": "viking://user/test_user/skills/b"},
            ],
            "total": 2,
        }

    monkeypatch.setattr(mcp_endpoint, "install_skills", fake_install_skills)

    result = await add_skill(path="https://github.com/org/skills")

    assert "Skill added: viking://user/test_user/skills/a" in result
    assert "Skill added: viking://user/test_user/skills/b" in result


async def test_add_skill_local_path_issues_skill_upload_token(service):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    result = await add_skill(
        path="/tmp/skills/pdf",
        skills=["pdf"],
        target_uri="viking://agent/skills",
    )

    assert "local skill detected" in result.lower()
    assert "zip -r" in result
    match = re.search(r"/api/v1/resources/temp_upload\?token=([A-Za-z0-9]+)", result)
    assert match
    info = upload_token_store.peek(match.group(1))
    assert info.kind == "skill"
    assert info.skill_target_uri == "viking://agent/skills"
    assert info.skill_names == ["pdf"]
    upload_token_store.clear()


async def test_add_skill_rejects_a_target_below_a_skill_root_before_minting_a_token(service):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    token = _mcp_ctx.set(RequestContext(DEFAULT_CTX.user, Role.USER))
    try:
        result = await add_skill(path="/tmp/skills/pdf", target_uri="viking://~/skills/pdf")
    finally:
        _mcp_ctx.reset(token)

    assert result.startswith("INVALID_ARGUMENT: Unsupported skill root URI")
    assert "viking://agent/skills" in result
    assert upload_token_store._store == {}


async def test_add_skill_maps_a_shared_subpath_to_the_shared_root(service):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    result = await add_skill(path="/tmp/skills/pdf", target_uri="viking://agent/skills/pdf")

    token = re.search(r"temp_upload\?token=([A-Za-z0-9]+)", result).group(1)
    assert upload_token_store.peek(token).skill_target_uri == "viking://agent/skills"
    upload_token_store.clear()


async def test_add_skill_list_only_upload_says_nothing_is_installed(service):
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    result = await add_skill(path="/tmp/skills", list_only=True)

    assert "upload it to list the skills it contains" in result
    assert "installs nothing" in result
    assert "do NOT need to call add_skill" not in result
    upload_token_store.clear()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"path": "tos://bucket/skills/pdf"}, "unsupported skill source"),
        ({}, "provide 'data'"),
        ({"data": _skill_md("x"), "path": "/tmp/x"}, "not both"),
        ({"data": "/tmp/skills/pdf/SKILL.md"}, 'add_skill(path="/tmp/skills/pdf/SKILL.md")'),
        ({"path": "viking://agent/skills/pdf"}, "read its SKILL.md"),
        ({"data": _skill_md("x"), "target_uri": "viking://resources/x"}, "INVALID_URI:"),
    ],
)
async def test_add_skill_rejects_invalid_arguments(kwargs, expected):
    result = await add_skill(**kwargs)
    assert expected in result


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
    lower = result.lower()
    assert "gateway" in lower or "reverse proxy" in lower
    assert "openviking_name" in lower or "extra request headers" in lower
    assert "replay" in lower or "same headers" in lower
    upload_token_store.clear()


async def test_add_resource_local_path_uses_env_var_when_set(service, monkeypatch):
    from openviking.server.config import ServerConfig
    from openviking.server.upload_token_store import upload_token_store

    upload_token_store.clear()
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://my-ov.example.com")
    monkeypatch.setattr(
        "openviking.server.dependencies._server_config",
        ServerConfig(public_base_url="https://configured.example.com"),
    )
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


# ---------------------------------------------------------------------------
# download tool
# ---------------------------------------------------------------------------


@pytest.fixture
def _clear_download_tokens():
    from openviking.server.upload_token_store import download_token_store

    download_token_store.clear()
    yield download_token_store
    download_token_store.clear()


def _stat_service(monkeypatch, stat):
    monkeypatch.setattr(
        mcp_endpoint, "get_service", lambda: SimpleNamespace(fs=SimpleNamespace(stat=stat))
    )


async def test_download_tool_is_registered_read_only():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}

    tool = tools["download"]
    assert set(tool.inputSchema["properties"]) == {"uri"}
    assert tool.annotations.readOnlyHint is True
    assert tool.description.startswith("Get a short-lived URL for downloading")


async def test_download_mints_token_bound_to_identity_and_uri(monkeypatch, _clear_download_tokens):
    store = _clear_download_tokens
    stat = AsyncMock(return_value={"name": "design.pdf", "size": 2_516_582, "isDir": False})
    _stat_service(monkeypatch, stat)
    ctx = RequestContext(
        UserIdentifier("acct", "alice"), Role.USER, actor_peer_id="bot-a", from_oauth=True
    )
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://ov.example.com/")
    token_ctx = _mcp_ctx.set(ctx)
    try:
        result = await download("viking://~/specs/design.pdf")
    finally:
        _mcp_ctx.reset(token_ctx)

    stat.assert_awaited_once_with("viking://user/alice/specs/design.pdf", ctx=ctx, skip_count=True)
    assert "viking://user/alice/specs/design.pdf (design.pdf, 2.4 MB)" in result
    match = re.search(
        r"curl -fL -o \./design\.pdf 'https://ov\.example\.com/api/v1/content/download"
        r"\?token=([A-Za-z0-9]+)'",
        result,
    )
    assert match, result
    assert "no OpenViking API key needed" in result
    assert "replay the same headers" in result
    assert "expires in ~10 minutes" in result
    assert "OPENVIKING_PUBLIC_BASE_URL is not set" not in result
    info = store.peek(match.group(1))
    assert info.uri == "viking://user/alice/specs/design.pdf"
    assert (info.account_id, info.user_id, info.role) == ("acct", "alice", Role.USER)
    assert info.actor_peer_id == "bot-a"
    assert info.from_oauth is True


async def test_download_shell_quotes_untrusted_filename(monkeypatch, _clear_download_tokens):
    _stat_service(
        monkeypatch, AsyncMock(return_value={"name": "$(id).pdf", "size": 3, "isDir": False})
    )

    result = await download("viking://resources/$(id).pdf")

    assert "-o './$(id).pdf' " in result


async def test_download_rejects_directory_without_minting(monkeypatch, _clear_download_tokens):
    _stat_service(monkeypatch, AsyncMock(return_value={"name": "specs", "isDir": True}))

    result = await download("viking://resources/specs")

    assert "directory" in result
    assert "list or tree" in result
    assert _clear_download_tokens._store == {}


async def test_download_missing_file_fails_without_minting(service, _clear_download_tokens):
    with pytest.raises(NotFoundError):
        await download("viking://resources/does-not-exist.pdf")
    assert _clear_download_tokens._store == {}


async def test_download_denied_file_fails_without_minting(monkeypatch, _clear_download_tokens):
    _stat_service(monkeypatch, AsyncMock(side_effect=PermissionDeniedError("denied")))

    with pytest.raises(PermissionDeniedError):
        await download("viking://resources/secret.pdf")
    assert _clear_download_tokens._store == {}


async def test_download_checks_with_user_role_outside_api_key_mode(
    monkeypatch, _clear_download_tokens
):
    stat = AsyncMock(return_value={"name": "a.bin", "size": 10, "isDir": False})
    _stat_service(monkeypatch, stat)
    monkeypatch.setattr(
        mcp_endpoint,
        "get_server_config",
        lambda: SimpleNamespace(
            get_effective_auth_mode=lambda: "dev",
            upload_signed_ttl_seconds=600,
        ),
    )
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://ov.example.com")
    ctx = RequestContext(UserIdentifier("default", "default"), Role.ROOT)
    token_ctx = _mcp_ctx.set(ctx)
    try:
        await download("viking://resources/a.bin")
    finally:
        _mcp_ctx.reset(token_ctx)

    assert stat.await_args.kwargs["ctx"].role == Role.USER


async def test_download_inferred_base_url_adds_operator_note(monkeypatch, _clear_download_tokens):
    from openviking.server.mcp_endpoint import _request_url_ctx

    _stat_service(
        monkeypatch, AsyncMock(return_value={"name": "a.bin", "size": 10, "isDir": False})
    )
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL", raising=False)
    token = _request_url_ctx.set({"x_forwarded_host": "ov.public.example.com"})
    try:
        result = await download("viking://resources/a.bin")
    finally:
        _request_url_ctx.reset(token)

    assert "https://ov.public.example.com/api/v1/content/download?token=" in result
    assert "(a.bin, 10 B)" in result
    assert "OPENVIKING_PUBLIC_BASE_URL is not set" in result


async def test_add_resource_temp_file_id_lookalike_in_path_is_rejected(service):
    result = await add_resource(path="upload_abc123.pdf")
    assert "looks like a temp_file_id" in result.lower()
    assert 'temp_file_id="upload_abc123.pdf"' in result


async def test_add_resource_neither_path_nor_temp_file_id(service):
    result = await add_resource()
    assert "error" in result.lower()
    assert "path" in result.lower() or "temp_file_id" in result.lower()


@pytest.mark.usefixtures("public_resource_dns")
async def test_add_resource_remote_url_is_ingested(service, monkeypatch):
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured.update(kwargs, path=path)
        return {"root_uri": "viking://resources/test_remote"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)
    result = await add_resource(
        path="https://example.com/x.md", tags=["team=search"], tag_mode="append"
    )
    assert "Resource added: viking://resources/test_remote" in result
    assert captured["path"] == "https://example.com/x.md"
    assert captured["enforce_public_remote_targets"] is True
    assert captured["tags"] == ["team=search"]
    assert captured["tag_mode"] == "append"


async def test_add_resource_remote_async_result_exposes_task_id(service, monkeypatch):
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured.update(kwargs)
        return {
            "status": "accepted",
            "task_id": "ov-task-123",
            "connector_task_key": "connector-task-456",
        }

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    result = await add_resource(path="tos://bucket/docs", parent="viking://resources/imports")

    assert "task_id: ov-task-123" in result
    assert "processing in background" in result
    assert captured["parent"] == "viking://resources/imports"
    assert captured["create_parent"] is True
    assert captured["to"] is None


async def test_add_resource_declared_add_type_is_forwarded(service, monkeypatch):
    captured = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return {"task_id": "ov-task-123"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    # A non-URL source: only reaches the service because add_type is declared;
    # without it this path shape would be treated as a local file.
    result = await add_resource(
        path="space:home",
        add_type="feishu",
        to="viking://resources/feishu",
    )

    assert "task_id: ov-task-123" in result
    assert captured["path"] == "space:home"
    assert captured["add_type"] == "feishu"
    assert captured["to"] == "viking://resources/feishu"


async def test_add_resource_declared_add_type_rejects_temp_file_id(service):
    result = await add_resource(temp_file_id="upload_abc.md", add_type="feishu")

    assert result == "Error: add_type cannot be combined with temp_file_id."


async def test_add_resource_declared_add_type_requires_exact_to(service):
    result = await add_resource(path="space:home", add_type="feishu")

    assert result == "Error: add_type requires an exact 'to' target."


async def test_add_resource_declared_add_type_rejects_parent(service):
    result = await add_resource(
        path="space:home",
        add_type="feishu",
        to="viking://resources/feishu",
        parent="viking://resources/imports",
    )

    assert result == "Error: add_type cannot be combined with parent."


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
        captured.update(kwargs)
        return {"root_uri": "viking://resources/from_tfid"}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)

    result = await add_resource(
        temp_file_id=tfid,
        parent="viking://resources/imports",
    )
    assert "Resource added" in result
    assert captured["path"] == str(target.resolve())
    assert captured["allow_local_path_resolution"] is True
    assert captured["parent"] == "viking://resources/imports"
    assert captured["create_parent"] is True
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


@pytest.mark.usefixtures("public_resource_dns")
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
    wm = service.watch_manager
    return await wm.create_task(
        path="https://example.com/foo",
        account_id=DEFAULT_CTX.account_id,
        user_id=DEFAULT_CTX.user.user_id,
        original_role="root",
        to_uri=to_uri,
        watch_interval=1440.0,
    )


async def test_list_watches_with_seed(service):
    assert await list_watches() == "No watch tasks."
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
    repeated = await cancel_watch(to_uri=task.to_uri)
    assert repeated == f"No watch task found for {task.to_uri}"


# ---------------------------------------------------------------------------
# forget tool
# ---------------------------------------------------------------------------


async def test_forget_by_uri_deletes_memory(service):
    ctx = DEFAULT_CTX
    uri = "viking://~/memories/test_forget.md"
    canonical_uri = "viking://user/test_user/memories/test_forget.md"
    await service.viking_fs.mkdir("viking://user/test_user/memories", ctx=ctx, exist_ok=True)
    await service.viking_fs.write(canonical_uri, "test data", ctx=ctx)

    token = _mcp_ctx.set(RequestContext(DEFAULT_CTX.user, Role.USER))
    try:
        result = await forget(uri=uri)
    finally:
        _mcp_ctx.reset(token)
    assert "deleted" in result.lower()
    assert "test_forget.md" in result
    assert not await service.viking_fs.exists(canonical_uri, ctx=ctx)


async def test_forget_by_uri_deletes_resource(service):
    """forget should work on any viking:// URI, not just memories."""
    ctx = DEFAULT_CTX
    uri = "viking://resources/test_forget_resource.md"
    await service.viking_fs.mkdir("viking://resources", ctx=ctx, exist_ok=True)
    await service.viking_fs.write(uri, "resource data", ctx=ctx)

    result = await forget(uri=uri)
    assert "deleted" in result.lower()
    assert not await service.viking_fs.exists(uri, ctx=ctx)


async def test_forget_directory_requires_recursive(service):
    ctx = DEFAULT_CTX
    dir_uri = "viking://resources/test_forget_dir"
    child_uri = f"{dir_uri}/child.md"
    await service.viking_fs.mkdir(dir_uri, ctx=ctx, exist_ok=True)
    await service.viking_fs.write(child_uri, "child data", ctx=ctx)

    with pytest.raises(InvalidArgumentError, match="recursive"):
        await forget(uri=dir_uri)
    assert await service.viking_fs.read_file(child_uri, ctx=ctx) == "child data"

    result = await forget(uri=dir_uri, recursive=True)
    assert "deleted" in result.lower()
    assert not await service.viking_fs.exists(dir_uri, ctx=ctx)


@pytest.mark.parametrize(
    ("uri", "sentinel_uri", "expected_message"),
    [
        # 'viking://user' is the container of user spaces, never a deletable path.
        (
            "viking://user",
            "viking://user/test_user/memories/forget_root_guard.md",
            "Deleting viking://user is not supported",
        ),
        (
            "viking://~",
            "viking://user/test_user/memories/forget_home_guard.md",
            "namespace root",
        ),
        (
            "viking://resources",
            "viking://resources/forget_root_guard/sentinel.md",
            "namespace root",
        ),
    ],
)
async def test_forget_rejects_namespace_roots_for_non_root(
    service, uri, sentinel_uri, expected_message
):
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.USER,
    )
    parent_uri = sentinel_uri.rsplit("/", 1)[0]
    await service.viking_fs.mkdir(parent_uri, ctx=ctx, exist_ok=True)
    await service.viking_fs.write(sentinel_uri, "must survive", ctx=ctx)

    token = _mcp_ctx.set(ctx)
    try:
        with pytest.raises(PermissionDeniedError, match=re.escape(expected_message)):
            await forget(uri=uri, recursive=True)
    finally:
        _mcp_ctx.reset(token)

    assert (await service.viking_fs.read(sentinel_uri, ctx=ctx)).decode("utf-8") == "must survive"


# ---------------------------------------------------------------------------
# write tool
# ---------------------------------------------------------------------------


async def test_write_default_creates_and_replaces_file(service):
    uri = "viking://resources/test_write/notes.md"
    for mode, content, expected in (
        ("append", "first\n", "first\n"),
        ("append", "second\n", "first\nsecond\n"),
        ("create", "new", "new"),
        (None, "replacement", "replacement"),
    ):
        options = {"mode": mode} if mode else {}
        result = await write(uri=uri, content=content, wait=True, **options)
        assert "Wrote" in result and uri in result
        assert f"mode={mode or 'replace'}" in result
        assert await service.fs.read(uri, ctx=DEFAULT_CTX) == expected


async def test_write_create_rejects_disallowed_extension(service):
    with pytest.raises(InvalidArgumentError):
        await write(uri="viking://resources/test_write_ext.csv", content="a,b\n", mode="create")


async def test_write_rejects_derived_semantic_file(service):
    with pytest.raises(InvalidArgumentError):
        await write(uri="viking://resources/test_write_derived/.abstract.md", content="x")
    with pytest.raises(InvalidArgumentError):
        await write(uri="viking://resources/test_write_derived/.relations.json", content="x")


async def test_write_and_live_context_read_roundtrip(service, monkeypatch):
    uri = "viking://user/test_user/resources/test_write_roundtrip/profile.md"
    await write(uri=uri, content="name: ada\n", wait=True)
    assert "name: ada" in await read(uris=uri)

    tool = Tool(
        name="current",
        inputSchema={"type": "object", "properties": {"city": {"type": "string"}}},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    monkeypatch.setattr(
        service.mcps,
        "_discover",
        AsyncMock(return_value=(SimpleNamespace(protocolVersion="2025-03-26"), [tool])),
    )
    upstream = SimpleNamespace(
        initialize=AsyncMock(),
        call_tool=AsyncMock(
            return_value=CallToolResult(content=[], structuredContent={"temperature": 23})
        ),
    )

    @asynccontextmanager
    async def session(_server, _headers, _timeout_seconds):
        yield upstream

    monkeypatch.setattr(service.mcps, "_session", session)
    tools_root = "viking://agent/tools"
    tool_uri = f"{tools_root}/weather/current.json"
    await service.mcps.add(
        "weather",
        "https://mcp.example.com",
        {},
        DEFAULT_CTX,
        target_uri=tools_root,
        wait=True,
    )
    await service.mcps.mount(
        tool_uri,
        uri,
        "Weather",
        None,
        60,
        {"city": "Beijing"},
        DEFAULT_CTX,
        tags=["env=prod"],
        wait=True,
    )

    assert json.loads(await read(uris=uri)) == {"temperature": 23}
    assert upstream.call_tool.await_count == 1
    assert upstream.call_tool.await_args.kwargs["arguments"] == {"city": "Beijing"}
    raw = await content_router.read(uri=uri, offset=0, limit=-1, raw=True, _ctx=DEFAULT_CTX)
    assert tool_uri in raw.result
    paged = await content_router.read(uri=uri, offset=1, limit=1, raw=False, _ctx=DEFAULT_CTX)
    assert paged.result == raw.result.splitlines(keepends=True)[1]
    assert await read(uris=uri, offset=1, limit=1) == paged.result
    assert upstream.call_tool.await_count == 1
    visible = await content_router.read(uri=uri, offset=0, limit=-1, raw=False, _ctx=DEFAULT_CTX)
    assert json.loads(visible.result) == {"temperature": 23}

    await service.fs.set_tags(uri, ["env=stg"], "replace", False, DEFAULT_CTX)
    mounts = await service.mcps.mounts("weather", None, DEFAULT_CTX, target_uri=tools_root)
    assert mounts["mounts"][0]["node_uri"] == uri
    assert mounts["mounts"][0]["tags"] == ["env=stg"]
    other = RequestContext(user=UserIdentifier.the_default_user("bob"), role=Role.USER)
    assert (await service.mcps.mounts("weather", None, other, target_uri=tools_root))[
        "mounts"
    ] == []

    tool.annotations = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
    await service.mcps.sync("weather", DEFAULT_CTX, target_uri=tools_root, wait=True)
    assert "Invoke the MCP tool explicitly" in await read(uris=uri)
    assert upstream.call_tool.await_count == 2
    invoked = await mcp_endpoint.invoke_mcp_tool(tool_uri, {"city": "Beijing"})
    assert invoked.structuredContent == {"temperature": 23}
    assert upstream.call_tool.await_count == 3

    await service.mcps.unmount(uri, DEFAULT_CTX, wait=True)
    assert (await service.mcps.mounts("weather", None, DEFAULT_CTX, target_uri=tools_root))[
        "mounts"
    ] == []
    assert not await service.viking_fs.exists(uri, ctx=DEFAULT_CTX)


async def test_edit_sequential_edits_compose(service):
    uri = "viking://resources/test_edit_order.md"
    await write(uri=uri, content="foo bar baz\n", wait=True)
    changed = await edit(uri=uri, old_string="bar", new_string="qux", wait=True)
    assert "Edited" in changed
    assert await service.fs.read(uri, ctx=DEFAULT_CTX) == "foo qux baz\n"
    await edit(uri=uri, old_string="foo qux", new_string="hello", wait=True)
    body = await service.fs.read(uri, ctx=DEFAULT_CTX)
    assert body == "hello baz\n"


async def test_edit_requires_unique_match_unless_replace_all(service):
    uri = "viking://resources/test_edit_multi.md"
    await write(uri=uri, content="dup\ndup\n", wait=True)
    with pytest.raises(InvalidArgumentError, match="matches 2 locations"):
        await edit(uri=uri, old_string="dup", new_string="x")
    assert await service.fs.read(uri, ctx=DEFAULT_CTX) == "dup\ndup\n"
    await edit(uri=uri, old_string="dup", new_string="x", replace_all=True, wait=True)
    body = await service.fs.read(uri, ctx=DEFAULT_CTX)
    assert body == "x\nx\n"


async def test_edit_line_ending_mismatch_says_so(service):
    uri = "viking://resources/test_edit_crlf.md"
    await write(uri=uri, content="line 1\r\nline 2\r\n")
    with pytest.raises(InvalidArgumentError, match="CRLF/LF"):
        await edit(uri=uri, old_string="line 1\nline 2\n", new_string="x")
    with pytest.raises(InvalidArgumentError, match=r"not found in \S+\. Re-read"):
        await edit(uri=uri, old_string="zzz", new_string="x")


async def test_edit_empty_old_string_fails(service):
    uri = "viking://resources/test_edit_empty.md"
    await write(uri=uri, content="alpha\n")
    with pytest.raises(InvalidArgumentError, match="must not be empty"):
        await edit(uri=uri, old_string="", new_string="x")


async def test_edit_on_missing_file_fails(service):
    with pytest.raises(NotFoundError):
        await edit(uri="viking://resources/test_edit_ghost.md", old_string="a", new_string="b")


async def test_edit_noop_reports_no_changes(service):
    uri = "viking://resources/test_edit_noop.md"
    await write(uri=uri, content="same\n")
    result = await edit(uri=uri, old_string="same", new_string="same")
    assert "No changes" in result


async def test_edit_memory_file_preserves_metadata(service):
    uri = "viking://user/test_user/memories/preferences/test_edit_memory.md"
    await write(uri=uri, content="likes: tea\n")
    raw_before = await service.fs.read(uri, ctx=DEFAULT_CTX)
    assert "MEMORY_FIELDS" in raw_before

    await edit(uri=uri, old_string="tea", new_string="coffee")

    raw_after = await service.fs.read(uri, ctx=DEFAULT_CTX)
    assert "MEMORY_FIELDS" in raw_after
    assert "coffee" in raw_after
    visible = await service.fs.read_visible(uri, ctx=DEFAULT_CTX)
    assert visible.strip() == "likes: coffee"


@pytest.mark.parametrize("role", [Role.USER, Role.ADMIN, Role.ROOT])
async def test_write_home_alias_uri(service, role):
    """Every MCP request role writes `viking://~/...` under its effective user."""
    user_ctx = RequestContext(DEFAULT_CTX.user, role)
    canonical = f"viking://user/{DEFAULT_CTX.user.user_id}/memories/preferences/home_alias.md"
    token = _mcp_ctx.set(user_ctx)
    try:
        result = await write(uri="viking://~/memories/preferences/home_alias.md", content="x")
        listing = await list_tool(uri="viking://~/memories/preferences")
        read_back = await read(uris="viking://~/memories/preferences/home_alias.md")
    finally:
        _mcp_ctx.reset(token)
    # Responses echo the expanded canonical URI, never the alias.
    assert canonical in result
    assert "viking://~" not in result
    assert "home_alias.md" in listing
    assert "x" in read_back
    assert "viking://~" not in read_back
    visible = await service.fs.read_visible(canonical, ctx=DEFAULT_CTX)
    assert visible.strip() == "x"


async def test_write_plain_file_directly_at_user_root(service):
    uri = "viking://user/test_user/persona.md"
    result = await write(uri=uri, content="# Persona\n")
    assert uri in result
    assert "# Persona" in await read(uris=uri)


async def test_edit_user_root_file_via_canonical_uri(service):
    uri = "viking://user/test_user/project/editable.md"
    created = await write(uri=uri, content="before\n", wait=True)
    assert uri in created
    assert await read(uri=uri) == "before\n"

    result = await edit(uri=uri, old_string="before", new_string="after", wait=True)

    assert "viking://user/test_user/project/editable.md" in result
    assert await read(uri=uri) == "after\n"


async def test_write_user_managed_subtree_rejected(service):
    with pytest.raises(InvalidArgumentError, match="user root"):
        await write(uri="viking://user/test_user/sessions/fake-session.md", content="x")
    with pytest.raises(InvalidArgumentError, match="user root"):
        await write(uri="viking://user/test_user/skills/demo/SKILL.md", content="x")


async def test_write_tool_schema_is_portable():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    props = tools["write"].inputSchema["properties"]
    assert props["uri"]["type"] == "string"
    assert props["content"]["type"] == "string"
    assert props["mode"]["enum"] == ["replace", "append", "create"]
    assert props["resolve_conflicts"]["type"] == "boolean"
    assert props["resolve_conflicts"]["default"] is False
    assert "resolve_conflicts" not in tools["write"].inputSchema.get("required", [])
    assert {"uri", "content"} <= set(tools["write"].inputSchema.get("required", []))


async def test_edit_tool_schema_is_portable():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    props = tools["edit"].inputSchema["properties"]
    assert props["uri"]["type"] == "string"
    assert props["old_string"]["type"] == "string"
    assert props["new_string"]["type"] == "string"
    assert props["replace_all"]["type"] == "boolean"
    assert {"uri", "old_string", "new_string"} <= set(tools["edit"].inputSchema.get("required", []))


# ---------------------------------------------------------------------------
# grep tool
# ---------------------------------------------------------------------------


async def test_grep_no_matches(service):
    result = await grep(uri="viking://resources", pattern=["pattern_a_xyz", "pattern_b_xyz"])
    assert "No matches found" in result
    assert "pattern_a_xyz" in result
    assert "pattern_b_xyz" in result


async def test_grep_case_insensitive(service):
    root = "viking://resources/grep-case"
    uri = f"{root}/notes.md"
    await service.viking_fs.write_file(uri, "First line\na test result\n", ctx=DEFAULT_CTX)
    assert (await grep(uri=root, pattern="TEST")) == "No matches found for pattern(s): TEST"
    assert f"\n{uri}\n  L2 [test]: a test result" in await grep(uri=root, pattern="test")
    result = await grep(uri=root, pattern="TEST", case_insensitive=True)
    assert f"\n{uri}\n  L2 [TEST]: a test result" in result


async def test_grep_forwards_and_renders_tags(service, monkeypatch):
    captured = {}

    async def fake_grep(uri, pattern, **kwargs):
        captured.update(uri=uri, pattern=pattern, **kwargs)
        return {
            "matches": [
                {
                    "uri": "viking://resources/guide.md",
                    "line": 3,
                    "content": "tagged text",
                    "tags": ["team=search"],
                }
            ]
        }

    monkeypatch.setattr(service.fs, "grep", fake_grep)

    result = await grep(
        uri="viking://resources",
        pattern="tagged",
        tags=["team=search"],
        include_tags=True,
    )

    assert captured["tags"] == ["team=search"]
    assert captured["include_tags"] is True
    assert '\nviking://resources/guide.md tags=["team=search"]' in result


# ---------------------------------------------------------------------------
# glob tool
# ---------------------------------------------------------------------------


async def test_glob_no_matches(service):
    result = await glob(pattern="**/zzz_nonexistent_*.xyz")
    assert "No files found" in result


async def test_glob_with_uri_scope(service):
    root = "viking://resources/glob-scope"
    match_uri = f"{root}/nested/guide.md"
    wrong_extension = f"{root}/nested/guide.txt"
    outside = "viking://resources/glob-other/nested/other.md"
    for uri in (match_uri, wrong_extension, outside):
        await service.viking_fs.write_file(uri, "hello glob\n", ctx=DEFAULT_CTX)
    result = await glob(pattern="**/*.md", uri=root)
    assert result.splitlines() == ["Found 1 file(s):", f"  {match_uri}"]


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def test_mcp_route_sets_scope_route(app):
    """The /mcp route must resolve ``scope["route"]`` on match so the
    observability middleware's route-template lookup attributes MCP traffic
    to ``/mcp`` instead of falling back to ``/__unmatched__``."""
    from starlette.routing import Match

    (mcp_route,) = [r for r in app.routes if getattr(r, "path", None) == "/mcp"]

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    match, child_scope = mcp_route.matches(scope)

    assert match != Match.NONE
    assert child_scope["route"] is mcp_route


def test_mcp_route_unmatched_paths_keep_falling_back(app):
    """Non-matching paths must not gain ``scope["route"]`` — the 404 fallback
    to ``/__unmatched__`` (low-cardinality protection) stays intact."""
    from starlette.routing import Match

    mcp_route = next(r for r in app.routes if getattr(r, "path", None) == "/mcp")

    scope = {"type": "http", "method": "POST", "path": "/mcp-does-not-exist", "headers": []}
    match, child_scope = mcp_route.matches(scope)

    assert match == Match.NONE
    assert "route" not in child_scope


async def test_mcp_middleware_stamps_and_uses_root_identity_for_home_alias():
    """Identity resolved from the auth headers must be stamped onto the outer
    request's root span attributes, so MCP traffic is audited under the real
    account/user instead of ``__unknown__``."""
    from openviking.telemetry.span_models import RootSpanAttributes

    root_attrs = RootSpanAttributes(
        http_method="POST",
        http_route="/mcp",
        request_id="req-test",
    )

    seen = {}

    async def downstream(scope, receive, send):
        ctx = _get_ctx()
        seen["ctx"] = ctx
        seen["uri"] = _resolve_mcp_workspace_uri("viking://~/memories", ctx)
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

    async def seed_root_span(scope, receive, send):
        # Mirrors the outer observability middleware attaching root_span_attrs
        # to scope["state"] before routing.
        if scope["type"] == "http":
            scope.setdefault("state", {})["root_span_attrs"] = root_attrs
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=seed_root_span)
    async with httpx.AsyncClient(transport=transport, base_url="http://ov.test") as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"X-OpenViking-Account": "acct-1", "X-OpenViking-User": "user-1"},
        )

    assert response.status_code == 200
    assert root_attrs.account_id == "acct-1"
    assert root_attrs.user_id == "user-1"
    assert seen["ctx"].role == Role.ROOT
    assert seen["ctx"].account_id == "acct-1"
    assert seen["uri"] == "viking://user/user-1/memories"


# ---- tree tool ----


async def test_tree_renders_indented_hierarchy(service):
    root = "viking://resources/test_tree"
    await write(uri=f"{root}/top.md", content="top\n")
    await write(uri=f"{root}/sub/a.md", content="alpha\n")
    await write(uri=f"{root}/sub/deeper/b.md", content="beta\n")

    result = await tree(uri=root)
    assert result.splitlines()[1:] == [
        "sub/",
        "  deeper/",
        "    b.md (5 B)",
        "  a.md (6 B)",
        "top.md (4 B)",
    ]

    shallow = await tree(uri=root, level_limit=1)
    assert shallow.splitlines()[1:] == ["sub/", "top.md (4 B)"]
    assert await tree(uri=root, node_limit=5) == result
    partial = await tree(uri=root, node_limit=1)
    assert partial.splitlines()[1] == "sub/"
    assert "(truncated at node_limit=1" in partial


async def test_tree_empty_directory(service):
    result = await tree(uri="viking://resources/test_tree_nope")
    assert result == "(nothing under viking://resources/test_tree_nope)"


async def test_tree_include_abstract_skips_not_ready_placeholders(service):
    root = "viking://user/test_user/skills"
    skill = f"{root}/pdf"
    files = {
        f"{skill}/.abstract.md": "name: pdf\ndescription: Fill PDF forms",
        f"{skill}/scripts/.abstract.md": f"# {skill}/scripts [Directory abstract is not ready]",
        f"{skill}/references/.abstract.md": "[.abstract.md is not ready]",
        f"{skill}/SKILL.md": "# PDF",
    }
    for uri, body in files.items():
        await service.viking_fs.write_file(uri, body, ctx=DEFAULT_CTX)

    result = await tree(uri=root, include_abstract=True)
    assert "  - name: pdf description: Fill PDF forms" in result
    assert "not ready" not in result
    assert {"  scripts/", "  references/", "  SKILL.md (5 B)"} <= set(result.splitlines())


async def test_privacy_upsert_description_is_a_label_shown_by_the_tree(service):
    from mcp.server.fastmcp.exceptions import ToolError

    privacy = service.privacy_configs
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    description_schema = tools["privacy_upsert"].inputSchema["properties"]["description"]

    def tools_schema_max_length():
        return next(
            item["maxLength"]
            for item in [description_schema, *description_schema.get("anyOf", [])]
            if "maxLength" in item
        )

    async def upsert(**arguments):
        result = await mcp_endpoint.mcp.call_tool(
            "privacy_upsert", {"category": "credential", "target_key": "github", **arguments}
        )
        assert not isinstance(result, CallToolResult), result
        return result

    async def tree_targets():
        tree = await mcp_endpoint.mcp.call_tool("privacy_tree", {"category": "credential"})
        return {target["target_key"]: target for target in tree[1]["categories"][0]["targets"]}

    await upsert(values={"pat": "description-secret-1"}, description="GitHub PAT for acme")
    meta = await privacy.get_meta(DEFAULT_CTX, "credential", "github")
    assert meta.labels == {"description": "GitHub PAT for acme"}

    # Other labels survive a description change; value-only updates keep both.
    await privacy.patch_values(
        DEFAULT_CTX, "credential", "github", {}, labels={**meta.labels, "env": "prod"}
    )
    await upsert(values={"pat": "description-secret-2"})
    meta = await privacy.get_meta(DEFAULT_CTX, "credential", "github")
    assert meta.labels == {"description": "GitHub PAT for acme", "env": "prod"}
    await upsert(values={"pat": "description-secret-2"}, description="Rotated acme PAT")
    meta = await privacy.get_meta(DEFAULT_CTX, "credential", "github")
    assert meta.labels == {"description": "Rotated acme PAT", "env": "prod"}

    await privacy.upsert(DEFAULT_CTX, "credential", "plain", {"token": "plain-secret-1"})
    targets = await tree_targets()
    assert targets["github"]["description"] == "Rotated acme PAT"
    assert "env" not in json.dumps(targets["github"])
    assert "description" not in targets["plain"]

    assert tools_schema_max_length() == 500
    with pytest.raises(ToolError):
        await mcp_endpoint.mcp.call_tool(
            "privacy_upsert",
            {
                "category": "credential",
                "target_key": "github",
                "values": {"pat": "description-secret-3"},
                "description": "x" * 501,
            },
        )
    current = await privacy.get_current(DEFAULT_CTX, "credential", "github")
    assert current.values["pat"] == "description-secret-2"


async def test_privacy_tree_leaves_auto_out_unless_named(client, service):
    privacy = service.privacy_configs
    await privacy.upsert(DEFAULT_CTX, "credential", "github", {"pat": "tree-named-secret-1"})
    await privacy.insert_values(
        DEFAULT_CTX, "auto", "github-tree01", {"token": "tree-auto-secret-1"}
    )
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    assert 'category="auto"' in tools["privacy_tree"].description

    tree = await mcp_endpoint.mcp.call_tool("privacy_tree", {})
    assert [node["category"] for node in tree[1]["categories"]] == ["credential"]
    named = await mcp_endpoint.mcp.call_tool("privacy_tree", {"category": "auto"})
    [node] = named[1]["categories"]
    assert node["category"] == "auto"
    assert [target["target_key"] for target in node["targets"]] == ["github-tree01"]
    assert "tree-auto-secret-1" not in json.dumps(named[1])

    # The HTTP tree route keeps listing everything.
    response = await client.get(
        "/api/v1/privacy-configs/tree", headers={"X-OpenViking-User": DEFAULT_CTX.user.user_id}
    )
    assert response.status_code == 200, response.text
    assert [node["category"] for node in response.json()["result"]["categories"]] == [
        "auto",
        "credential",
    ]
