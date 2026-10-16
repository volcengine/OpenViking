# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Exercise the MCP-to-HTTP original-file download flow."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlparse

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from mcp.types import CallToolRequest, CallToolRequestParams
from starlette.routing import Route

from openviking.server import auth, mcp_endpoint
from openviking.server.auth.plugins import DevAuthPlugin
from openviking.server.download_token_store import DownloadTokenStore
from openviking.server.identity import RequestContext, Role
from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.routers import content
from openviking_cli.exceptions import NotFoundError, OpenVikingError, PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def download_context(monkeypatch):
    ctx = RequestContext(
        UserIdentifier("download-account", "alice"),
        Role(Role.ROOT),
        actor_peer_id="test-peer",
        api_key="must-not-appear-in-tool-output",
    )
    token = mcp_endpoint._mcp_ctx.set(ctx)
    store = DownloadTokenStore()
    monkeypatch.setattr(mcp_endpoint, "download_token_store", store)
    monkeypatch.setattr(auth, "download_token_store", store)
    monkeypatch.setenv("OPENVIKING_PUBLIC_BASE_URL", "https://ov.example.test/prefix/")
    yield ctx
    mcp_endpoint._mcp_ctx.reset(token)


async def call_get(uri):
    handler = mcp_endpoint.mcp._mcp_server.request_handlers[CallToolRequest]
    request = CallToolRequest(params=CallToolRequestParams(name="get", arguments={"uri": uri}))
    return (await handler(request)).model_dump(by_alias=True, exclude_none=True)


def download_app():
    app = FastAPI()
    app.state.config = SimpleNamespace(get_effective_auth_mode=lambda: "dev")
    app.state.auth_plugin = DevAuthPlugin()
    app.include_router(content.router)

    @app.exception_handler(OpenVikingError)
    async def handle_error(_request, exc):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[exc.code], content={"error": exc.code}
        )

    return app


async def test_get_is_discoverable_as_a_read_only_tool():
    tools = {tool.name: tool for tool in await mcp_endpoint.mcp.list_tools()}
    assert "get" in tools
    assert tools["get"].inputSchema["required"] == ["uri"]
    assert tools["get"].annotations is not None
    assert tools["get"].annotations.readOnlyHint is True
    assert tools["get"].annotations.destructiveHint is False


async def test_get_downloads_original_bytes_once_without_mcp_credentials(
    monkeypatch, download_context, tmp_path
):
    uri = "viking://user/alice/resources/原始 file & 'quote'.bin"
    data = b"\x00\xfforiginal bytes\r\n\x80"
    source = tmp_path / "source.bin"
    source.write_bytes(data)
    downloaded = []

    async def stat(target, *, ctx, skip_count):
        assert target == uri
        assert ctx.user == download_context.user
        assert ctx.actor_peer_id == "test-peer"
        return {"name": "原始 file & 'quote'.bin", "isDir": False, "size": len(data)}

    async def read_file_bytes(target, *, ctx):
        assert target == uri
        assert ctx.user == download_context.user
        assert ctx.actor_peer_id == "test-peer"
        assert ctx.api_key is None
        downloaded.append(target)
        return source.read_bytes()

    service = SimpleNamespace(fs=SimpleNamespace(stat=stat, read_file_bytes=read_file_bytes))
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(content, "get_service", lambda: service)
    result = await call_get("viking://~/resources/原始 file & 'quote'.bin")
    assert result["isError"] is False, result
    instruction = result["content"][0]["text"]
    assert uri in instruction
    assert download_context.api_key not in instruction
    assert downloaded == []  # Preparing a download must not read the binary into MCP.
    url = re.search(r"https://ov\.example\.test/prefix/api/v1/content/download/\S+", instruction)
    assert url is not None, instruction
    path = urlparse(url.group()).path.removeprefix("/prefix")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=download_app()), base_url="https://ov.example.test"
    ) as client:
        response = await client.get(
            path,
            params={"uri": "viking://resources/some-other-file.bin"},
            headers={
                "X-OpenViking-Account": "other-account",
                "X-OpenViking-User": "mallory",
                "X-OpenViking-Actor-Peer": "other-peer",
            },
        )
        assert response.status_code == 200, response.text
        assert response.content == data
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["cache-control"] == "no-store"
        assert (await client.get(path)).status_code == 401
        original = await client.get(
            "/api/v1/content/download",
            params={"uri": uri},
            headers={
                "X-OpenViking-Account": "download-account",
                "X-OpenViking-User": "alice",
                "X-OpenViking-Actor-Peer": "test-peer",
            },
        )
        assert original.status_code == 200, original.text
        assert original.content == response.content
        assert original.headers["content-disposition"] == response.headers["content-disposition"]
    assert downloaded == [uri, uri]


@pytest.mark.parametrize(
    ("stat_result", "error_code"),
    [
        ({"isDir": True, "name": "folder", "size": 0}, "INVALID_ARGUMENT"),
        (NotFoundError("viking://resources/missing.bin", "file"), "NOT_FOUND"),
        (PermissionDeniedError("read permission required"), "PERMISSION_DENIED"),
    ],
)
async def test_get_rejects_unreadable_files(monkeypatch, download_context, stat_result, error_code):
    stat = (
        AsyncMock(side_effect=stat_result)
        if isinstance(stat_result, Exception)
        else AsyncMock(return_value=stat_result)
    )
    monkeypatch.setattr(
        mcp_endpoint, "get_service", lambda: SimpleNamespace(fs=SimpleNamespace(stat=stat))
    )
    result = await call_get("viking://resources/missing.bin")
    assert result["isError"] is True
    assert result["structuredContent"]["error"]["code"] == error_code
    assert "/api/v1/content/download/" not in result["content"][0]["text"]


async def test_get_download_rechecks_file_access(monkeypatch, download_context):
    service = SimpleNamespace(
        fs=SimpleNamespace(
            stat=AsyncMock(return_value={"name": "secret.bin", "isDir": False, "size": 100}),
            read_file_bytes=AsyncMock(side_effect=PermissionDeniedError("access revoked")),
        )
    )
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(content, "get_service", lambda: service)
    result = await call_get("viking://resources/secret.bin")
    assert result["isError"] is False, result
    instruction = result["content"][0]["text"]
    url = re.search(r"https://ov\.example\.test/prefix/api/v1/content/download/\S+", instruction)
    assert url is not None, instruction
    path = urlparse(url.group()).path.removeprefix("/prefix")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=download_app()), base_url="https://ov.example.test"
    ) as client:
        response = await client.get(path)
        assert response.status_code == 403
        assert response.json()["error"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("base_url", "headers"),
    [
        ("https://secure.example.test", {}),
        ("http://secure.example.test", {"X-Forwarded-Proto": "https"}),
    ],
)
async def test_get_preserves_https_in_download_url(
    monkeypatch, download_context, base_url, headers
):
    monkeypatch.delenv("OPENVIKING_PUBLIC_BASE_URL")
    monkeypatch.setattr(mcp_endpoint, "get_server_config", lambda: None)
    monkeypatch.setattr(
        mcp_endpoint,
        "get_service",
        lambda: SimpleNamespace(
            fs=SimpleNamespace(stat=AsyncMock(return_value={"isDir": False, "size": 1}))
        ),
    )

    async def downstream(scope, receive, send):
        result = await call_get("viking://resources/report.pdf")
        await JSONResponse(result)(scope, receive, send)

    app = download_app()
    app.routes.append(
        Route("/mcp", endpoint=mcp_endpoint._IdentityASGIMiddleware(downstream), methods=["POST"])
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=base_url
    ) as client:
        response = await client.post("/mcp", headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["isError"] is False, result
    assert "https://secure.example.test/api/v1/content/download/" in result["content"][0]["text"]


async def test_original_download_still_requires_authentication():
    app = download_app()
    # Without an initialized auth plugin, an ordinary download must not gain token access.
    app.state.auth_plugin = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://ov.example.test"
    ) as client:
        response = await client.get(
            "/api/v1/content/download", params={"uri": "viking://resources/secret.bin"}
        )
        assert response.status_code == 401
