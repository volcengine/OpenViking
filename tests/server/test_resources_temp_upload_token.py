# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the signed-token upload path on POST /api/v1/resources/temp_upload.

The MCP ``add_resource`` tool mints a short-lived ``?token=`` for local-file paths. A POST
carrying that token (and no API key) is authorized by the token alone, and the server
finishes ingestion in-request — the agent never posts a ``temp_file_id`` back. An API-key
POST keeps the legacy behavior of just storing the file and returning its ``temp_file_id``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from openviking.server.identity import Role
from openviking.server.upload_token_store import upload_token_store


@pytest.fixture(autouse=True)
def _reset_token_store():
    upload_token_store.clear()
    yield
    upload_token_store.clear()


async def test_token_upload_auto_ingests_and_returns_result(upload_temp_dir: Path, monkeypatch):
    """An upload keeps the issuing authority, but cannot outlive a role downgrade."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from urllib.parse import parse_qs, urlparse

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from openviking.server import mcp_endpoint, resource_ingest
    from openviking.server.auth.plugins import ApiKeyAuthPlugin
    from openviking.server.config import ServerConfig
    from openviking.server.identity import RequestContext
    from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
    from openviking.server.routers.resources import router
    from openviking.storage.acl import AclSpec
    from openviking.storage.viking_fs._access import _AccessMixin
    from openviking_cli.exceptions import OpenVikingError
    from openviking_cli.session.user_id import UserIdentifier

    current_role = Role.ADMIN
    config = ServerConfig(auth_mode="api_key", root_api_key="test-root")
    app = FastAPI()
    app.state.config = config
    app.state.auth_plugin = ApiKeyAuthPlugin()
    app.state.api_key_manager = SimpleNamespace(
        has_user=lambda *_: current_role is not None,
        get_user_role=lambda *_: current_role,
        get_user_group_ids=lambda *_: (),
        is_deleting=lambda *_: False,
    )
    app.include_router(router)

    @app.exception_handler(OpenVikingError)
    async def handle_error(_request, exc):
        return JSONResponse(
            status_code=ERROR_CODE_TO_HTTP_STATUS[exc.code], content={"error": exc.code}
        )

    fs = _AccessMixin()
    fs.acl_manager = SimpleNamespace(is_enabled=AsyncMock(return_value=False))
    acl = AclSpec(acl_mode="restricted", entries=[{"principal": "user:bob", "level": "read"}])
    target = "viking://resources/hello.md"
    imported = []

    async def ingest(*, path, ctx, acl, to, **_kwargs):
        # Exercise the actual kernel authorization, without an embedding/native stack.
        assert ctx.user == UserIdentifier("acct", "user")
        assert ctx.actor_peer_id == "bot-a"
        await fs.prepare_acl_update(to, acl, ctx)
        imported.append(Path(path).read_bytes())
        return {"root_uri": to}

    service = SimpleNamespace(resources=SimpleNamespace(add_resource=ingest))
    monkeypatch.setattr(mcp_endpoint, "get_service", lambda: service)
    monkeypatch.setattr(mcp_endpoint, "get_server_config", lambda: config)
    monkeypatch.setattr(resource_ingest, "get_service", lambda: service)
    identity = RequestContext(
        UserIdentifier("acct", "user"), Role.ADMIN, actor_peer_id="bot-a", from_oauth=True
    )

    async def issue():
        identity_token = mcp_endpoint._mcp_ctx.set(identity)
        try:
            instruction = await mcp_endpoint.add_resource(path="/tmp/hello.md", to=target, acl=acl)
        finally:
            mcp_endpoint._mcp_ctx.reset(identity_token)
        upload_url = next(line.strip() for line in instruction.splitlines() if "?token=" in line)
        return parse_qs(urlparse(upload_url).query)["token"][0]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:

        async def upload(token):
            return await client.post(
                "/api/v1/resources/temp_upload",
                params={"token": token},
                headers={
                    "X-OpenViking-Account": "other-account",
                    "X-OpenViking-User": "spoofed",
                    "X-OpenViking-Role": "admin",
                    "X-OpenViking-Actor-Peer": "other-peer",
                },
                files={"file": ("hello.md", b"hello world", "text/markdown")},
            )

        token = await issue()
        response = await upload(token)
        assert response.status_code == 200, response.text
        assert response.json()["result"]["root_uri"] == target
        assert "temp_file_id" not in response.json()["result"]
        assert (await upload(token)).status_code == 401

        # An outstanding token loses authority when its user is downgraded.
        identity.from_oauth = False
        token = await issue()
        current_role = Role.USER
        assert (await upload(token)).status_code == 401

        # A token issued as USER does not gain ADMIN authority after promotion.
        identity.role = Role.USER
        token = await issue()
        current_role = Role.ADMIN
        assert (await upload(token)).status_code == 403

        # Removing the user also invalidates an outstanding token.
        identity.role = Role.ADMIN
        token = await issue()
        current_role = None
        assert (await upload(token)).status_code == 401

    assert imported == [b"hello world"]


async def test_token_upload_ingest_error_is_not_reported_as_success(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    """A business-error result from add_resource must surface as an HTTP error, not 200 ok."""

    async def failing_add_resource(*, path, ctx, **kwargs):
        return {"status": "error", "code": "PROCESSING_ERROR", "errors": ["parse failed"]}

    monkeypatch.setattr(service.resources, "add_resource", failing_add_resource)
    token, _ = upload_token_store.issue("acct", "user", role=Role.USER, ttl_seconds=600)
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("bad.md", b"junk", "text/markdown")},
    )
    assert resp.status_code >= 400, resp.text
    assert resp.json().get("status") == "error"


async def test_token_upload_oversize_rejected(
    client: httpx.AsyncClient, upload_temp_dir: Path, app
):
    """Size cap is enforced by TempUploadStore before ingestion; oversize maps to 413."""
    app.state.config.temp_upload.shared_max_size_bytes = 16
    token, _ = upload_token_store.issue("acct", "user", role=Role.USER, ttl_seconds=600)
    big = b"x" * 64
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("big.bin", big, "text/plain")},
    )
    assert resp.status_code == 413


async def test_apikey_temp_upload_returns_temp_file_id(
    client: httpx.AsyncClient, upload_temp_dir: Path
):
    """No token → legacy path: store the file and return its temp_file_id unchanged."""
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        files={"file": ("legacy.md", b"legacy", "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    tfid = resp.json()["result"]["temp_file_id"]
    assert (upload_temp_dir / tfid).is_file()


def _skill_zip(name: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: Zipped skill for upload tests\n---\n\n# {name}\n",
        )
        zf.writestr(f"{name}/scripts/run.sh", "#!/bin/sh\necho ok\n")
    return buffer.getvalue()


async def test_skill_token_upload_installs_zipped_skill_directory(
    client: httpx.AsyncClient, service, upload_temp_dir: Path
):
    token, _ = upload_token_store.issue(
        "acct", "user", role=Role.USER, ttl_seconds=600, kind="skill"
    )
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("zip-skill.zip", _skill_zip("zip-skill"), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["root_uri"].endswith("/skills/zip-skill")
    assert result["auxiliary_files"] == 1


async def test_skill_token_upload_list_only_does_not_install(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    async def fail_add_skill(**_kwargs):
        raise AssertionError("list_only must not install")

    monkeypatch.setattr(service.resources, "add_skill", fail_add_skill)
    token, _ = upload_token_store.issue(
        "acct", "user", role=Role.USER, ttl_seconds=600, kind="skill", list_only=True
    )
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("zip-skill.zip", _skill_zip("zip-skill"), "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["result"]["skills"] == [
        {
            "name": "zip-skill",
            "description": "Zipped skill for upload tests",
            "path": "zip-skill",
        }
    ]
