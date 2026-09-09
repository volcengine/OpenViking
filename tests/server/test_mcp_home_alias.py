# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Every MCP tool must expand ``viking://~`` before it uses a URI.

The home alias is expanded at the request boundary only: `resolve_request_uri` needs
the caller's identity, and the canonical parser deliberately fails closed on a literal
``~`` rather than inventing a namespace for it. So a tool that skips the boundary does
not merely lose the alias, it fails with ``Invalid scope '~'``.

That makes the property per tool rather than per resolver, which is how #4829 saw
``list``/``glob`` reject the alias while ``find``/``search`` accepted it. The resolver
itself is covered in `test_mcp_endpoint.py`; what is pinned here is that every tool
routes through one, including tools added later.

These tests deliberately take no service fixture. Both resolvers are patched to raise,
so each tool stops at its URI boundary and no tool body runs — nothing is read, written
or deleted, and the assertion is exactly "the boundary was reached".
"""

from types import SimpleNamespace

import pytest

import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.dependencies import set_service
from openviking.server.identity import RequestContext, Role
from openviking.server.mcp_endpoint import _mcp_ctx
from openviking_cli.session.user_id import UserIdentifier

CTX = RequestContext(user=UserIdentifier.the_default_user("test_user"), role=Role.ROOT)

HOME_ALIAS_URI = "viking://~/memories/probe.md"

# Tools that name their URI by its role instead of spelling "uri".
URI_PARAMS_BY_ROLE = {"add_resource": ("to", "parent")}
# Tools that legitimately accept no viking:// URI at all.
TOOLS_WITHOUT_URI_PARAMS = {"remember", "list_watches", "health"}
# Arguments a specific parameter needs to reach its own boundary. `exclude_uris` is
# consumed by the context path, so list mode never gets as far as resolving it.
EXTRA_ARGS_BY_PARAM = {("search", "exclude_uris"): {"mode": "context"}}
# The smallest set of non-URI arguments that reaches each tool's URI boundary.
REQUIRED_NON_URI_ARGS = {
    "find": {"query": "anything"},
    "search": {"query": "anything"},
    "write": {"content": "anything"},
    "edit": {"old_string": "a", "new_string": "b"},
    "grep": {"pattern": "anything"},
    "glob": {"pattern": "*"},
}


class ResolverReached(Exception):
    """Raised by the patched resolvers so no tool body runs past its URI boundary."""

    def __init__(self, uri: str):
        super().__init__(uri)
        self.uri = uri


@pytest.fixture(autouse=True)
def _identity_and_service():
    # Tools call get_service() before they resolve their URI, so a service has to be
    # wired even though the patched resolvers stop every tool before it is touched.
    set_service(SimpleNamespace())
    token = _mcp_ctx.set(CTX)
    yield
    _mcp_ctx.reset(token)
    set_service(None)


def uri_params(tool) -> tuple[str, ...]:
    properties = (tool.inputSchema or {}).get("properties", {}) or {}
    named = tuple(name for name in properties if "uri" in name.lower())
    return named or URI_PARAMS_BY_ROLE.get(tool.name, ())


@pytest.mark.asyncio
async def test_every_registered_tool_declares_its_uri_parameters():
    """A tool added later cannot be skipped by the coverage test below in silence.

    That test only reaches parameters `uri_params` can find, so a new tool whose URI
    parameter is named by role would be quietly exempt. This pins both lists instead.
    """
    tools = await mcp_endpoint.mcp.list_tools()
    assert tools, "no MCP tools registered"

    misdeclared = [
        tool.name
        for tool in tools
        if bool(uri_params(tool)) == (tool.name in TOOLS_WITHOUT_URI_PARAMS)
    ]

    assert not misdeclared, (
        "each of these either takes a viking:// URI that uri_params cannot find, or "
        "takes none and belongs in TOOLS_WITHOUT_URI_PARAMS: " + ", ".join(misdeclared)
    )


@pytest.mark.asyncio
async def test_every_tool_routes_its_uri_through_the_home_alias_boundary(monkeypatch):
    def _raise(uri, ctx, **kwargs):
        raise ResolverReached(uri)

    monkeypatch.setattr(mcp_endpoint, "_resolve_mcp_workspace_uri", _raise)
    monkeypatch.setattr(mcp_endpoint, "validate_content_target_uri", _raise)

    unresolved = []
    for tool in await mcp_endpoint.mcp.list_tools():
        for param in uri_params(tool):
            # `read` takes a list of URIs; every other parameter takes one.
            value = [HOME_ALIAS_URI] if param.endswith("s") else HOME_ALIAS_URI
            kwargs = {
                param: value,
                **REQUIRED_NON_URI_ARGS.get(tool.name, {}),
                **EXTRA_ARGS_BY_PARAM.get((tool.name, param), {}),
            }
            try:
                await mcp_endpoint.mcp._tool_manager.get_tool(tool.name).fn(**kwargs)
            except ResolverReached as reached:
                assert reached.uri == HOME_ALIAS_URI
                continue
            except Exception as exc:  # noqa: BLE001 — anything else means it never resolved
                unresolved.append(f"{tool.name}.{param} ({type(exc).__name__}: {exc})")
                continue
            unresolved.append(f"{tool.name}.{param} (returned without resolving)")

    assert not unresolved, (
        "these tool parameters never reached a URI boundary, so viking://~ stays "
        "literal in them and the canonical parser will reject it: " + ", ".join(unresolved)
    )
