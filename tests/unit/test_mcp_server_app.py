from inspect import signature, unwrap
from types import SimpleNamespace

import pytest
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import ValidationError

import openviking
import openviking.server.app as server_app
import openviking.server.mcp_endpoint as mcp_endpoint
from openviking.server.config import ServerConfig
from openviking.server.mcp_endpoint import _IdentityASGIMiddleware


def test_create_mcp_app_applies_streamable_http_options(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_streamable_http_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(routes=[SimpleNamespace(app=downstream)])

    monkeypatch.setattr(mcp_endpoint.mcp, "streamable_http_app", fake_streamable_http_app)

    app = mcp_endpoint.create_mcp_app()

    assert isinstance(app, _IdentityASGIMiddleware)
    assert app.app is downstream
    assert captured["stateless_http"] is True
    assert captured["max_request_body_size"] == 4 * 1024 * 1024
    assert captured["transport_security"].enable_dns_rebinding_protection is False


def test_create_mcp_app_applies_custom_request_body_limit(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_streamable_http_app(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(routes=[SimpleNamespace(app=downstream)])

    monkeypatch.setattr(mcp_endpoint.mcp, "streamable_http_app", fake_streamable_http_app)

    app = mcp_endpoint.create_mcp_app(max_request_body_size=8 * 1024 * 1024)

    assert isinstance(app, _IdentityASGIMiddleware)
    assert captured["max_request_body_size"] == 8 * 1024 * 1024


def test_create_app_passes_configured_mcp_request_body_limit(monkeypatch):
    captured = {}

    async def downstream(scope, receive, send):
        del scope, receive, send

    def fake_create_mcp_app(*, max_request_body_size):
        captured["max_request_body_size"] = max_request_body_size
        return downstream

    monkeypatch.setattr(mcp_endpoint, "create_mcp_app", fake_create_mcp_app)
    config = ServerConfig(mcp_max_request_body_size_bytes=8 * 1024 * 1024)

    server_app.create_app(config=config)

    assert captured["max_request_body_size"] == 8 * 1024 * 1024


@pytest.mark.parametrize("value", [0, -1])
def test_server_config_rejects_non_positive_mcp_request_body_limit(value):
    with pytest.raises(ValidationError):
        ServerConfig(mcp_max_request_body_size_bytes=value)


def test_mcp_server_advertises_openviking_version():
    assert mcp_endpoint.mcp.version == openviking.__version__


@pytest.mark.asyncio
async def test_mcp_dispatch_preserves_expected_openviking_error_message():
    with pytest.raises(ToolError) as exc_info:
        await mcp_endpoint.mcp.call_tool(
            "list",
            {"uri": "viking://", "offset": -1},
        )

    assert type(exc_info.value) is ToolError
    assert str(exc_info.value) == (
        "Error executing tool list: offset must be greater than or equal to 0"
    )


@pytest.mark.asyncio
async def test_mcp_error_adapter_preserves_tool_contract():
    tools = await mcp_endpoint.mcp.list_tools()
    assert [tool.name for tool in tools] == [
        "find",
        "search",
        "read",
        "list",
        "tree",
        "remember",
        "write",
        "edit",
        "add_resource",
        "add_skill",
        "list_watches",
        "cancel_watch",
        "grep",
        "glob",
        "forget",
        "health",
    ]

    baseline = MCPServer("openviking-schema-baseline")
    for registered_tool in mcp_endpoint.mcp._tool_manager.list_tools():
        assert hasattr(registered_tool.fn, "__wrapped__")
        assert signature(registered_tool.fn) == signature(unwrap(registered_tool.fn))
        baseline.add_tool(
            unwrap(registered_tool.fn),
            name=registered_tool.name,
            title=registered_tool.title,
            description=registered_tool.description,
            annotations=registered_tool.annotations,
            icons=registered_tool.icons,
            meta=registered_tool.meta,
            structured_output=registered_tool.output_schema is not None,
        )

    for baseline_tool in baseline._tool_manager.list_tools():
        baseline_tool.parameters = mcp_endpoint._portable_schema(baseline_tool.parameters)

    baseline_tools = await baseline.list_tools()
    assert [tool.model_dump(mode="json", by_alias=True) for tool in tools] == [
        tool.model_dump(mode="json", by_alias=True) for tool in baseline_tools
    ]
