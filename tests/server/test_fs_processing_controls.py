# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Public filesystem processing-mode contracts without external models."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from openviking.server.app import create_app
from openviking.server.auth import get_request_context
from openviking.server.config import ServerConfig
from openviking.server.identity import RequestContext, Role
from openviking.server.routers import filesystem, resources, snapshot, webdav
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture
def api(monkeypatch):
    ctx = RequestContext(user=UserIdentifier("acct", "alice"), role=Role.ROOT)
    fs = SimpleNamespace(
        rm=AsyncMock(return_value={"semantic_status": "skipped", "vector_status": "complete"}),
        cp=AsyncMock(return_value={"semantic_status": "skipped", "vector_status": "complete"}),
        mv=AsyncMock(
            return_value={
                "semantic_status": "skipped",
                "vector_status": "queued",
                "task_id": "index-1",
            }
        ),
        restore=AsyncMock(return_value={}),
        restore_file_version=AsyncMock(return_value={}),
        stat=AsyncMock(return_value={"isDir": False}),
    )
    svc = SimpleNamespace(
        fs=fs,
        viking_fs=SimpleNamespace(write_file=AsyncMock()),
        resources=SimpleNamespace(summarize=AsyncMock(), build_index=AsyncMock()),
    )
    svc.sessions = None
    app = create_app(config=ServerConfig(), service=svc)
    for module in (filesystem, resources, snapshot, webdav):
        monkeypatch.setattr(module, "get_service", lambda: svc)
    app.dependency_overrides[get_request_context] = lambda: ctx
    return app, svc, ctx


@pytest.mark.parametrize("alias", ["fs", "resources"])
@pytest.mark.parametrize("operation", ["rm", "cp", "mv"])
async def test_http_modes_reach_service_and_preserve_status(api, alias, operation):
    app, svc, _ = api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if operation == "rm":
            response = await client.delete(
                f"/api/v1/{alias}",
                params={"uri": "viking://resources/a.md", "processing_mode": "vectors_only"},
            )
        else:
            response = await client.post(
                f"/api/v1/{alias}/{operation}",
                json={
                    "from_uri": "viking://resources/a.md",
                    "to_uri": "viking://resources/b.md",
                    "processing_mode": "vectors_only",
                },
            )
    assert response.status_code == 200, response.text
    assert getattr(svc.fs, operation).await_args.kwargs["processing_mode"] == "vectors_only"
    assert response.json()["result"]["semantic_status"] == "skipped"
    if operation == "mv":
        assert response.json()["result"]["task_id"] == "index-1"


@pytest.mark.parametrize("operation", ["rm", "cp", "mv"])
async def test_http_invalid_mode_rejected_before_mutation(api, operation):
    app, svc, _ = api
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        if operation == "rm":
            response = await client.delete(
                "/api/v1/fs", params={"uri": "viking://resources/a.md", "processing_mode": "bad"}
            )
        else:
            response = await client.post(
                f"/api/v1/fs/{operation}",
                json={
                    "from_uri": "viking://resources/a.md",
                    "to_uri": "viking://resources/b.md",
                    "processing_mode": "bad",
                },
            )
    assert response.status_code == 400
    getattr(svc.fs, operation).assert_not_awaited()


@pytest.mark.parametrize("file_mode", [True, False])
@pytest.mark.parametrize("mode", ["omitted", None, "vectors_only", "semantic_and_vectors", "bad"])
async def test_snapshot_processing_mode_contract(api, file_mode, mode):
    app, svc, _ = api
    body = (
        {"path": "viking://resources/a.md", "restore_commit_id": "a" * 40}
        if file_mode
        else {"project_dir": "viking://resources/project", "source_commit": "a" * 40}
    )
    if mode != "omitted":
        body["processing_mode"] = mode
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/snapshot/restore", json=body)
    rejected = mode == "bad" or (not file_mode and mode == "semantic_and_vectors")
    if rejected:
        assert response.status_code == 400, response.text
        svc.fs.restore.assert_not_awaited()
        svc.fs.restore_file_version.assert_not_awaited()
    else:
        assert response.status_code == 200, response.text
        if file_mode:
            assert svc.fs.restore_file_version.await_args.kwargs.get(
                "processing_mode", "semantic_and_vectors"
            ) == (
                mode if mode in {"vectors_only", "semantic_and_vectors"} else "semantic_and_vectors"
            )
        else:
            svc.fs.restore.assert_awaited_once()


@pytest.mark.parametrize("operation", ["PUT", "DELETE", "MOVE"])
@pytest.mark.parametrize("mode", ["vectors_only", "bad"])
async def test_webdav_processing_header(api, monkeypatch, operation, mode):
    app, svc, _ = api
    monkeypatch.setattr(webdav, "_ensure_parent_directory", AsyncMock(return_value={"isDir": True}))
    headers = {
        "X-OpenViking-Processing-Mode": mode,
        "Destination": "http://test/webdav/resources/b.md",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            operation, "/webdav/resources/a.md", headers=headers, content="hello"
        )
    if mode == "bad":
        assert response.status_code == 400
        svc.fs.rm.assert_not_awaited()
        svc.fs.mv.assert_not_awaited()
        svc.viking_fs.write_file.assert_not_awaited()
    elif operation == "PUT":
        assert response.status_code == 204
        svc.viking_fs.write_file.assert_awaited_once()
        svc.resources.summarize.assert_not_awaited()
        svc.resources.build_index.assert_awaited_once()
    else:
        assert response.status_code == 204
        assert svc.fs.rm.await_args.kwargs["processing_mode"] == "vectors_only"
        if operation == "MOVE":
            assert svc.fs.mv.await_args.kwargs["processing_mode"] == "vectors_only"


@pytest.mark.parametrize("operation", ["write", "edit", "forget"])
async def test_mcp_processing_mode_passthrough(api, monkeypatch, operation):
    import openviking.server.mcp_endpoint as mcp

    _, svc, ctx = api
    monkeypatch.setattr(mcp, "get_service", lambda: svc)
    svc.fs.write = AsyncMock(
        return_value={"written_bytes": 5, "semantic_status": "skipped", "vector_status": "queued"}
    )
    svc.fs.read_visible = AsyncMock(return_value="old")
    token = mcp._mcp_ctx.set(ctx)
    try:
        kwargs = {"uri": "viking://resources/a.md", "processing_mode": "vectors_only"}
        if operation == "write":
            kwargs["content"] = "hello"
        elif operation == "edit":
            kwargs.update(old_string="old", new_string="new")
        await getattr(mcp, operation)(**kwargs)
    finally:
        mcp._mcp_ctx.reset(token)
    method = svc.fs.rm if operation == "forget" else svc.fs.write
    assert method.await_args.kwargs["processing_mode"] == "vectors_only"
