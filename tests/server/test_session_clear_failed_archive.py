# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for the clear-failed-archive session resolve endpoint (#2294)."""

import json

import httpx
import pytest
import pytest_asyncio

# These tests exercise the real session storage layer, which requires both the
# RAGFS native binding and the local vectordb native engine. CI builds those
# from source; pure-Python dev sandboxes don't have them. Match the failure
# mode of the rest of the server suite by skipping (rather than erroring) when
# the natives can't be loaded.
ragfs_python = pytest.importorskip("ragfs_python")
from openviking.storage.vectordb import engine as _vectordb_engine  # noqa: E402

if getattr(_vectordb_engine, "ENGINE_VARIANT", "unavailable") == "unavailable":
    pytest.skip(
        "vectordb native engine unavailable in this dev sandbox",
        allow_module_level=True,
    )

from openviking.server.app import create_app  # noqa: E402
from openviking.server.config import ServerConfig  # noqa: E402
from openviking.server.dependencies import set_service  # noqa: E402
from openviking.server.identity import RequestContext, Role  # noqa: E402
from openviking_cli.session.user_id import UserIdentifier  # noqa: E402

ROOT_KEY = "test-root-secret-for-clear-failed"


async def _make_session(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/v1/sessions", json={})
    assert resp.status_code == 200
    return resp.json()["result"]["session_id"]


async def _write_failed_marker(
    service,
    session_id: str,
    archive_id: str,
    *,
    extra_files: dict[str, str] | None = None,
) -> str:
    """Write a fake .failed.json marker (and optionally other archive files).

    Uses the same default identity that the dev-mode HTTP client uses, so the
    on-disk session URI here matches the one the route resolves.
    """
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    archive_uri = f"{session.uri}/history/{archive_id}"

    payload = {
        "stage": "memory_extraction",
        "error": "synthetic failure for tests",
        "failed_at": "2026-06-15T00:00:00Z",
    }
    await session._viking_fs.write_file(
        uri=f"{archive_uri}/.failed.json",
        content=json.dumps(payload),
        ctx=session.ctx,
    )
    for name, content in (extra_files or {}).items():
        await session._viking_fs.write_file(
            uri=f"{archive_uri}/{name}",
            content=content,
            ctx=session.ctx,
        )
    return archive_uri


async def test_clears_marker_when_data_missing(client: httpx.AsyncClient, service):
    """Marker exists and the archive directory has nothing else → clear succeeds."""
    session_id = await _make_session(client)
    archive_id = "archive_001"
    await _write_failed_marker(service, session_id, archive_id)

    resp = await client.post(f"/api/v1/sessions/{session_id}/archives/{archive_id}/clear-failed")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["result"]["cleared"] is True
    assert body["result"]["session_id"] == session_id
    assert body["result"]["archive_id"] == archive_id
    assert body["result"]["data_present"] is False
    assert body["result"]["force"] is False

    # And the marker is now gone, so a follow-up commit no longer trips the
    # FAILED_PRECONDITION guard. We add a message first so the commit path
    # actually exercises the blocking-archive check.
    await client.post(
        f"/api/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "after fix-archive"},
    )
    commit_resp = await client.post(f"/api/v1/sessions/{session_id}/commit")
    assert commit_resp.status_code == 200, commit_resp.text
    commit_body = commit_resp.json()
    assert commit_body["status"] == "ok"


async def test_409_when_data_still_present(client: httpx.AsyncClient, service):
    """If the archive dir still has real content, default behavior refuses with 409."""
    session_id = await _make_session(client)
    archive_id = "archive_001"
    await _write_failed_marker(
        service,
        session_id,
        archive_id,
        extra_files={"messages.jsonl": '{"role":"user"}\n'},
    )

    resp = await client.post(f"/api/v1/sessions/{session_id}/archives/{archive_id}/clear-failed")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "CONFLICT"

    # Marker still present.
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()
    marker = await session._viking_fs.read_file(
        f"{session.uri}/history/{archive_id}/.failed.json", ctx=session.ctx
    )
    assert marker  # still there


async def test_force_clears_with_data_present(client: httpx.AsyncClient, service):
    """force=true clears the marker even when archive data is present.

    The archive data directory contents are NOT touched — only the marker is
    removed, so the operator's data residue is preserved for inspection.
    """
    session_id = await _make_session(client)
    archive_id = "archive_001"
    await _write_failed_marker(
        service,
        session_id,
        archive_id,
        extra_files={"messages.jsonl": '{"role":"user"}\n'},
    )

    resp = await client.post(
        f"/api/v1/sessions/{session_id}/archives/{archive_id}/clear-failed?force=true"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["result"]["cleared"] is True
    assert body["result"]["force"] is True
    assert body["result"]["data_present"] is True

    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    session = service.sessions.session(ctx, session_id)
    await session.load()

    # Marker is gone — read_file raises NotFoundError.
    from openviking_cli.exceptions import NotFoundError as _NotFoundError

    with pytest.raises(_NotFoundError):
        await session._viking_fs.read_file(
            f"{session.uri}/history/{archive_id}/.failed.json", ctx=session.ctx
        )

    # Data file is still there.
    surviving = await session._viking_fs.read_file(
        f"{session.uri}/history/{archive_id}/messages.jsonl", ctx=session.ctx
    )
    assert "user" in surviving


async def test_404_when_no_marker(client: httpx.AsyncClient):
    """If there is no .failed.json marker for archive_id, return 404."""
    session_id = await _make_session(client)

    resp = await client.post(f"/api/v1/sessions/{session_id}/archives/archive_999/clear-failed")
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "NOT_FOUND"


@pytest_asyncio.fixture(scope="function")
async def auth_app(service):
    """App with root_api_key configured so the auth layer is active."""
    from openviking.server.api_keys import APIKeyManager

    config = ServerConfig(root_api_key=ROOT_KEY)
    app = create_app(config=config, service=service)
    set_service(service)
    manager = APIKeyManager(root_key=ROOT_KEY, viking_fs=service.viking_fs)
    await manager.load()
    app.state.api_key_manager = manager
    return app


@pytest_asyncio.fixture(scope="function")
async def auth_client(auth_app):
    transport = httpx.ASGITransport(app=auth_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_auth_required(auth_client: httpx.AsyncClient):
    """Anonymous request must be rejected by the auth layer."""
    resp = await auth_client.post("/api/v1/sessions/some-session/archives/archive_001/clear-failed")
    assert resp.status_code in (401, 403), resp.text
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] in ("UNAUTHENTICATED", "PERMISSION_DENIED")
