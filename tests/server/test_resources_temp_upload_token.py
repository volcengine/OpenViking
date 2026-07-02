# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the signed-token upload path on POST /api/v1/resources/temp_upload.

The MCP ``add_resource`` tool mints a short-lived ``?token=`` for local-file paths. A POST
carrying that token (and no API key) is authorized by the token alone, and the server
finishes ingestion in-request — the agent never posts a ``temp_file_id`` back. An API-key
POST keeps the legacy behavior of just storing the file and returning its ``temp_file_id``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from openviking.server.upload_token_store import upload_token_store


@pytest.fixture(autouse=True)
def _reset_token_store():
    upload_token_store.clear()
    yield
    upload_token_store.clear()


def _issue(
    account_id: str = "acct",
    user_id: str = "user",
    *,
    to: str = "",
    reason: str = "",
    actor_peer_id: str = "",
):
    token, _ = upload_token_store.issue(
        account_id, user_id, ttl_seconds=600, to=to, reason=reason, actor_peer_id=actor_peer_id
    )
    return token


def _stub_ingest(service, monkeypatch, root_uri: str = "viking://resources/uploaded") -> dict:
    """Stub add_resource so token-path uploads don't run the full ingest pipeline."""
    captured: dict = {}

    async def fake_add_resource(*, path, ctx, **kwargs):
        captured["path"] = path
        captured["content"] = Path(path).read_bytes()
        captured["ctx"] = ctx
        captured["to"] = kwargs.get("to")
        captured["reason"] = kwargs.get("reason")
        captured["allow_local_path_resolution"] = kwargs.get("allow_local_path_resolution")
        return {"root_uri": root_uri}

    monkeypatch.setattr(service.resources, "add_resource", fake_add_resource)
    return captured


async def test_token_upload_auto_ingests_and_returns_result(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    captured = _stub_ingest(service, monkeypatch)
    token = _issue()
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("hello.md", b"hello world", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    # Auto-ingest returns the final resource, not a temp_file_id handshake.
    assert result["root_uri"] == "viking://resources/uploaded"
    assert "temp_file_id" not in result
    # The stored file was resolved and handed to add_resource as a local path.
    assert captured["allow_local_path_resolution"] is True
    assert captured["content"] == b"hello world"


async def test_token_upload_forwards_to_and_reason(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    captured = _stub_ingest(service, monkeypatch)
    token = _issue(to="viking://resources/team/proj", reason="quarterly report")
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("r.md", b"data", "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    assert captured["to"] == "viking://resources/team/proj"
    assert captured["reason"] == "quarterly report"


async def test_token_upload_uses_token_identity_ignoring_spoofed_headers(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    captured = _stub_ingest(service, monkeypatch)
    token = _issue(account_id="real_acct", user_id="real_user")
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("r.md", b"data", "text/markdown")},
        headers={"X-OpenViking-Account": "evil", "X-OpenViking-User": "attacker"},
    )
    assert resp.status_code == 200, resp.text
    ctx = captured["ctx"]
    assert ctx.user.account_id == "real_acct"
    assert ctx.user.user_id == "real_user"


async def test_token_upload_preserves_actor_peer_from_token(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    """Actor peer scope comes from the token (bound at mint), not the upload headers."""
    captured = _stub_ingest(service, monkeypatch)
    token = _issue(actor_peer_id="bot-a")
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("r.md", b"data", "text/markdown")},
        # A spoofed actor-peer header must be ignored; the token's peer wins.
        headers={"X-OpenViking-Actor-Peer": "evil-bot"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["ctx"].actor_peer_id == "bot-a"


async def test_token_upload_ingest_error_is_not_reported_as_success(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    """A business-error result from add_resource must surface as an HTTP error, not 200 ok."""

    async def failing_add_resource(*, path, ctx, **kwargs):
        return {"status": "error", "code": "PROCESSING_ERROR", "errors": ["parse failed"]}

    monkeypatch.setattr(service.resources, "add_resource", failing_add_resource)
    token = _issue()
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("bad.md", b"junk", "text/markdown")},
    )
    assert resp.status_code >= 400, resp.text
    assert resp.json().get("status") == "error"


async def test_token_upload_burns_token_on_use(
    client: httpx.AsyncClient, service, upload_temp_dir: Path, monkeypatch
):
    _stub_ingest(service, monkeypatch)
    token = _issue()
    resp1 = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("a.txt", b"first", "text/plain")},
    )
    assert resp1.status_code == 200, resp1.text

    resp2 = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": token},
        files={"file": ("a.txt", b"second", "text/plain")},
    )
    assert resp2.status_code == 401


async def test_token_upload_unknown_token(client: httpx.AsyncClient, upload_temp_dir: Path):
    resp = await client.post(
        "/api/v1/resources/temp_upload",
        params={"token": "ZZZZZZ"},
        files={"file": ("upload_abc.md", b"x", "text/plain")},
    )
    assert resp.status_code == 401


async def test_token_upload_oversize_rejected(
    client: httpx.AsyncClient, upload_temp_dir: Path, app
):
    """Size cap is enforced by TempUploadStore before ingestion; oversize maps to 413."""
    app.state.config.temp_upload.shared_max_size_bytes = 16
    token = _issue()
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
